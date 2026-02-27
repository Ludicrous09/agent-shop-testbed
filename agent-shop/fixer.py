"""Fix agent module — addresses code review feedback on a GitHub PR."""

import argparse
import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREE_BASE = Path("/tmp/agent-worktrees")

FIX_PROMPT_TEMPLATE = """\
You are an expert software engineer tasked with addressing code review feedback on a pull request.

## Review Feedback

{review_feedback}

## Your Task

Read the review comments above carefully, then fix every issue mentioned. Follow these steps:

1. Read each file mentioned in the review to understand the current code.
2. Fix each issue identified in the review. For each fix, note which file was changed and what was changed.
3. Run `pytest` to verify your fixes do not break any existing tests.
4. Run `ruff check --fix .` to fix any linting issues, followed by `ruff check .` to confirm no remaining issues.
5. Stage all changed files with `git add`.
6. Commit with the message: `[agent] fix: address review feedback`
7. Do NOT push — just commit locally.

Be thorough. Address every point raised in the review. If a comment suggests a test is missing, add it.
"""


@dataclass
class FixResult:
    success: bool
    pr_number: int
    commit_sha: str = ""
    fixes_summary: str = ""
    error: str = ""


class FixError(Exception):
    """Raised when the fix agent encounters an unrecoverable error."""


class FixAgent:
    def __init__(
        self,
        repo_path: str | Path,
        pr_number: int,
        model: str = "sonnet",
        timeout: int = 300,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.pr_number = pr_number
        self.model = model
        self.timeout = timeout
        self._owner, self._repo = self._parse_remote()
        self._worktree_path = WORKTREE_BASE / f"fix-{pr_number}"
        logger.info(
            "FixAgent initialised for PR #%d in %s/%s (model=%s)",
            pr_number,
            self._owner,
            self._repo,
            model,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fix(self) -> FixResult:
        logger.info("Starting fix for PR #%d", self.pr_number)

        try:
            review_feedback = self._get_review_feedback()
            logger.info("Got review feedback (%d chars)", len(review_feedback))

            head_branch = self._get_head_branch()
            logger.info("PR head branch: %s", head_branch)

            self._fetch_remote()
            self._setup_worktree(head_branch)

            prompt = FIX_PROMPT_TEMPLATE.format(review_feedback=review_feedback)
            logger.info("Built fix prompt (%d chars)", len(prompt))

            claude_output = self._run_claude(prompt)
            logger.info("Claude finished applying fixes")

            commit_sha = self._get_commit_sha()
            logger.info("Fix commit: %s", commit_sha)

            self._push(head_branch)
            logger.info("Pushed fix commit to %s", head_branch)

            fixes_summary = self._extract_summary(claude_output)
            self._post_comment(commit_sha, fixes_summary)
            logger.info("Posted fix comment on PR #%d", self.pr_number)

            return FixResult(
                success=True,
                pr_number=self.pr_number,
                commit_sha=commit_sha,
                fixes_summary=fixes_summary,
            )

        except (FixError, Exception) as exc:
            logger.error("Fix failed: %s", exc)
            return FixResult(
                success=False,
                pr_number=self.pr_number,
                error=str(exc),
            )
        finally:
            self._cleanup_worktree()

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _get_review_feedback(self) -> str:
        output = self._run_gh(["pr", "view", str(self.pr_number), "--json", "comments"])
        data = json.loads(output)
        comments = data.get("comments", [])

        tag = "[REVIEW: REQUEST_CHANGES]"
        for comment in reversed(comments):
            body = comment.get("body", "")
            if tag in body:
                idx = body.index(tag) + len(tag)
                feedback = body[idx:].strip()
                if feedback:
                    return feedback
                raise FixError(
                    f"Found {tag} in PR comment but no feedback text followed it"
                )

        raise FixError(
            f"No comment containing '{tag}' found on PR #{self.pr_number}"
        )

    def _get_head_branch(self) -> str:
        output = self._run_gh(
            ["pr", "view", str(self.pr_number), "--json", "headRefName"]
        )
        data = json.loads(output)
        return data["headRefName"]

    def _fetch_remote(self) -> None:
        logger.info("Fetching remote to ensure head branch is available locally")
        proc = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            logger.warning("git fetch origin failed: %s", proc.stderr[:300])

    def _setup_worktree(self, branch: str) -> None:
        WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

        if self._worktree_path.exists():
            logger.info("Removing existing worktree at %s", self._worktree_path)
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self._worktree_path)],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
            )

        logger.info(
            "Creating worktree at %s from origin/%s", self._worktree_path, branch
        )
        proc = subprocess.run(
            [
                "git",
                "worktree",
                "add",
                str(self._worktree_path),
                f"origin/{branch}",
            ],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(
                f"git worktree add failed (code {proc.returncode}): {proc.stderr[:500]}"
            )

        # Set the worktree's HEAD to track the remote branch so we can push
        proc = subprocess.run(
            ["git", "checkout", "-B", branch, f"origin/{branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(
                f"git checkout failed in worktree (code {proc.returncode}): {proc.stderr[:500]}"
            )

        logger.info("Worktree ready at %s", self._worktree_path)

    def _run_claude(self, prompt: str) -> str:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
            "--model",
            self.model,
        ]
        logger.info("Running claude in worktree (timeout=%ds)", self.timeout)
        try:
            proc = subprocess.run(
                cmd,
                cwd=self._worktree_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise FixError(f"Claude timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            raise FixError(
                f"Claude exited with code {proc.returncode}: {proc.stderr[:500]}"
            )

        try:
            envelope = json.loads(proc.stdout)
            cost = envelope.get("cost_usd", "unknown")
            turns = envelope.get("num_turns", "unknown")
            logger.info("Claude cost=$%s turns=%s", cost, turns)
            return envelope.get("result", proc.stdout)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Could not parse claude envelope JSON; using raw output")
            return proc.stdout

    def _get_commit_sha(self) -> str:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(
                f"git rev-parse HEAD failed (code {proc.returncode}): {proc.stderr[:300]}"
            )
        return proc.stdout.strip()

    def _push(self, branch: str) -> None:
        proc = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(
                f"git push failed (code {proc.returncode}): {proc.stderr[:500]}"
            )

    def _extract_summary(self, claude_output: str) -> str:
        """Pull a concise summary out of Claude's raw output for the PR comment."""
        text = claude_output.strip()
        # Return up to the first 1500 characters as the summary
        if len(text) > 1500:
            text = text[:1500].rsplit("\n", 1)[0] + "\n…(truncated)"
        return text or "Fixes applied as requested in the review."

    def _post_comment(self, commit_sha: str, fixes_summary: str) -> None:
        short_sha = commit_sha[:12]
        repo_url = f"https://github.com/{self._owner}/{self._repo}"
        commit_url = f"{repo_url}/commit/{commit_sha}"
        body = (
            f"## \U0001f527 Fixes Applied\n\n"
            f"Commit: [{short_sha}]({commit_url})\n\n"
            f"{fixes_summary}"
        )
        proc = subprocess.run(
            ["gh", "pr", "comment", str(self.pr_number), "--body", body],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(
                f"gh pr comment failed (code {proc.returncode}): {proc.stderr[:500]}"
            )
        logger.info("Fix comment posted on PR #%d", self.pr_number)

    def _cleanup_worktree(self) -> None:
        if not self._worktree_path.exists():
            return
        logger.info("Cleaning up worktree at %s", self._worktree_path)
        proc = subprocess.run(
            ["git", "worktree", "remove", "--force", str(self._worktree_path)],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            logger.warning(
                "git worktree remove failed (code %d): %s",
                proc.returncode,
                proc.stderr[:300],
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_gh(self, args: list[str]) -> str:
        cmd = ["gh"] + args
        proc = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(
                f"gh {' '.join(args)} failed (code {proc.returncode}): {proc.stderr[:500]}"
            )
        return proc.stdout

    def _parse_remote(self) -> tuple[str, str]:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.repo_path if self.repo_path.exists() else Path("."),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise FixError(f"Could not get git remote URL: {proc.stderr[:200]}")

        url = proc.stdout.strip()
        # SSH: git@github.com:owner/repo.git
        # HTTPS: https://github.com/owner/repo.git
        match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
        if not match:
            raise FixError(f"Could not parse owner/repo from remote URL: {url!r}")
        return match.group(1), match.group(2)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Fix agent — addresses PR review feedback")
    parser.add_argument("--pr", type=int, required=True, help="PR number to fix")
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Path to the git repo (default: cwd)",
    )
    parser.add_argument(
        "--model",
        default="sonnet",
        help="Claude model to use (default: sonnet)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Claude timeout in seconds (default: 300)",
    )
    args = parser.parse_args()

    agent = FixAgent(
        repo_path=args.repo_path,
        pr_number=args.pr,
        model=args.model,
        timeout=args.timeout,
    )

    result = agent.fix()

    if result.success:
        print(f"\nPR #{result.pr_number} — fixes applied successfully")
        print(f"Commit : {result.commit_sha}")
        print(f"\nSummary:\n{result.fixes_summary}")
    else:
        print(f"\nPR #{result.pr_number} — fix FAILED")
        print(f"Error  : {result.error}")
        raise SystemExit(1)
