"""Conflict resolution agent — automatically resolves merge conflicts in PRs."""

import argparse
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

WORKTREE_BASE = Path("/tmp/agent-worktrees")
_LARGE_PROMPT_THRESHOLD = 100 * 1024  # 100 KB

def _build_resolve_prompt(
    pr_description: str, main_changes: str, file_path: str, conflicted_content: str
) -> str:
    """Build the conflict-resolution prompt without using str.format().

    Avoids KeyError when the file content contains bare ``{placeholder}``
    patterns (e.g. Python format strings, Jinja templates, shell scripts).
    """
    return (
        "You are an expert software engineer resolving a merge conflict.\n\n"
        "## PR Description (what this PR is trying to do)\n\n"
        + pr_description
        + "\n\n## Changes merged into main since this PR branched (what caused the conflict)\n\n"
        + main_changes
        + "\n\n## Conflicted file: "
        + file_path
        + "\n\nThe file below contains conflict markers (<<<<<<<, =======, >>>>>>>).\n"
        "Your task is to resolve the conflicts by keeping BOTH sets of changes where possible.\n\n"
        + conflicted_content
        + "\n\nOutput ONLY the fully resolved file content with no conflict markers remaining.\n"
        "Do not include any explanation, markdown fences, or commentary — just the raw file content.\n"
    )


@dataclass
class ConflictResult:
    success: bool
    pr_number: int
    resolved_files: list[str] = field(default_factory=list)
    error: str = ""


class ConflictError(Exception):
    """Raised when conflict resolution encounters an unrecoverable error."""


class MergeabilityUnknownError(ConflictError):
    """Raised when GitHub has not yet computed PR mergeability (status = UNKNOWN).

    The caller should retry after a short delay rather than treating this as a
    hard failure or assuming there are no conflicts.
    """


class ConflictResolver:
    def __init__(
        self,
        repo_path: str | Path,
        pr_number: int,
        model: str = "sonnet",
        timeout: int = 300,
        worktree_base: Path | None = None,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.pr_number = pr_number
        self.model = model
        self.timeout = timeout
        _base = worktree_base if worktree_base is not None else WORKTREE_BASE
        self._worktree_path = _base / f"conflict-{pr_number}"
        logger.info(
            "ConflictResolver initialised for PR #%d (model=%s)",
            pr_number,
            model,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self) -> ConflictResult:
        logger.info("Checking PR #%d for merge conflicts", self.pr_number)
        try:
            if not self._has_conflicts():
                logger.info("PR #%d has no merge conflicts", self.pr_number)
                return ConflictResult(success=True, pr_number=self.pr_number)

            head_branch = self._get_head_branch()
            base_branch = self._get_base_branch()
            logger.info("PR head branch: %s  base branch: %s", head_branch, base_branch)

            self._fetch_remote()
            self._setup_worktree(head_branch)

            conflicted_files = self._get_conflicted_files_after_merge(base_branch)
            if not conflicted_files:
                logger.info("No conflicted files found after merge attempt")
                return ConflictResult(success=True, pr_number=self.pr_number)

            logger.info("Conflicted files: %s", conflicted_files)

            pr_description = self._get_pr_description()
            main_changes = self._get_main_changes_since_branch(base_branch)

            resolved: list[str] = []
            for file_path in conflicted_files:
                logger.info("Resolving conflicts in %s", file_path)
                try:
                    self._resolve_file(file_path, pr_description, main_changes)
                    resolved.append(file_path)
                except Exception as exc:
                    logger.error("Failed to resolve %s: %s", file_path, exc)
                    return ConflictResult(
                        success=False,
                        pr_number=self.pr_number,
                        error=f"Failed to resolve {file_path}: {exc}",
                    )

            self._commit_and_push(head_branch, resolved)
            self._post_comment(resolved)

            return ConflictResult(
                success=True,
                pr_number=self.pr_number,
                resolved_files=resolved,
            )

        except Exception as exc:
            logger.error("Conflict resolution failed: %s", exc)
            return ConflictResult(
                success=False,
                pr_number=self.pr_number,
                error=str(exc),
            )
        finally:
            self._cleanup_worktree()

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _has_conflicts(self) -> bool:
        output = self._run_gh(
            ["pr", "view", str(self.pr_number), "--json", "mergeable"]
        )
        data = json.loads(output)
        mergeable = data.get("mergeable", "UNKNOWN")
        logger.info("PR #%d mergeable status: %s", self.pr_number, mergeable)
        if mergeable == "UNKNOWN":
            raise MergeabilityUnknownError(
                f"PR #{self.pr_number} mergeability is UNKNOWN — "
                "GitHub has not yet computed it; retry after a short delay"
            )
        return mergeable == "CONFLICTING"

    def _get_head_branch(self) -> str:
        output = self._run_gh(
            ["pr", "view", str(self.pr_number), "--json", "headRefName"]
        )
        data = json.loads(output)
        return data["headRefName"]

    def _get_base_branch(self) -> str:
        output = self._run_gh(
            ["pr", "view", str(self.pr_number), "--json", "baseRefName"]
        )
        data = json.loads(output)
        return data["baseRefName"]

    def _get_pr_description(self) -> str:
        output = self._run_gh(
            ["pr", "view", str(self.pr_number), "--json", "body,title"]
        )
        data = json.loads(output)
        title = data.get("title", "")
        body = data.get("body", "")
        return f"**{title}**\n\n{body}".strip() or "No description provided."

    def _fetch_remote(self) -> None:
        logger.info("Fetching remote")
        proc = subprocess.run(
            ["git", "fetch", "origin"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConflictError(
                f"git fetch origin failed (code {proc.returncode}): {proc.stderr[:300]}"
            )

    def _setup_worktree(self, branch: str) -> None:
        self._worktree_path.parent.mkdir(parents=True, exist_ok=True)

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
            raise ConflictError(
                f"git worktree add failed (code {proc.returncode}): {proc.stderr[:500]}"
            )

        proc = subprocess.run(
            ["git", "checkout", "-B", branch, f"origin/{branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConflictError(
                f"git checkout failed in worktree (code {proc.returncode}): {proc.stderr[:500]}"
            )

        logger.info("Worktree ready at %s", self._worktree_path)

    def _get_conflicted_files_after_merge(self, base_branch: str) -> list[str]:
        """Run git merge origin/<base_branch> and return the list of conflicted files."""
        logger.info("Running git merge origin/%s to surface conflicts", base_branch)
        proc = subprocess.run(
            ["git", "merge", f"origin/{base_branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            logger.info("Merge completed without conflicts")
            return []

        diff_proc = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        return [f.strip() for f in diff_proc.stdout.strip().splitlines() if f.strip()]

    def _get_main_changes_since_branch(self, base_branch: str) -> str:
        """Get a summary of what changed in base_branch since this branch diverged."""
        merge_base_proc = subprocess.run(
            ["git", "merge-base", "HEAD", f"origin/{base_branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if merge_base_proc.returncode != 0:
            return "Unable to determine base branch changes."

        merge_base = merge_base_proc.stdout.strip()

        log_proc = subprocess.run(
            ["git", "log", "--oneline", f"{merge_base}..origin/{base_branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if not log_proc.stdout.strip():
            return "No additional commits on base branch since this branch was created."

        diff_proc = subprocess.run(
            ["git", "diff", "--stat", merge_base, f"origin/{base_branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        return (
            f"Commits merged into {base_branch}:\n{log_proc.stdout.strip()}\n\n"
            f"Files changed:\n{diff_proc.stdout.strip()}"
        )

    def _resolve_file(
        self, file_path: str, pr_description: str, main_changes: str
    ) -> None:
        """Use Claude to resolve conflicts in a single file."""
        full_path = self._worktree_path / file_path

        # Guard against path traversal attacks (e.g. "../../etc/shadow")
        try:
            resolved_full = full_path.resolve()
            worktree_resolved = self._worktree_path.resolve()
            if not resolved_full.is_relative_to(worktree_resolved):
                raise ConflictError(
                    f"Path traversal detected in file path: {file_path!r}"
                )
        except ValueError:
            raise ConflictError(
                f"Path traversal detected in file path: {file_path!r}"
            )

        if not full_path.exists():
            raise ConflictError(f"Conflicted file not found: {file_path}")

        conflicted_content = full_path.read_text()

        prompt = _build_resolve_prompt(
            pr_description=pr_description,
            main_changes=main_changes,
            file_path=file_path,
            conflicted_content=conflicted_content,
        )

        resolved_content = self._run_claude(prompt)
        resolved_content = self._strip_fences(resolved_content)

        if not resolved_content.strip():
            raise ConflictError(f"Claude returned empty content for {file_path}")

        if re.search(r"^[<=>]{7}", resolved_content, re.MULTILINE):
            raise ConflictError(
                f"Unresolved conflict markers remain in {file_path}"
            )

        full_path.write_text(resolved_content)
        logger.info(
            "Written resolved content to %s (%d chars)", file_path, len(resolved_content)
        )

    @staticmethod
    def _strip_fences(text: str) -> str:
        """Remove leading/trailing markdown code fences if present."""
        text = text.strip()
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        return text

    def _run_claude(self, prompt: str) -> str:
        use_stdin = len(prompt.encode("utf-8")) > _LARGE_PROMPT_THRESHOLD

        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
            "--model",
            self.model,
        ]
        if use_stdin:
            stdin_input: str | None = prompt
        else:
            cmd = [cmd[0], "-p", prompt] + cmd[1:]
            stdin_input = None

        logger.info("Running claude for conflict resolution (timeout=%ds)", self.timeout)
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = subprocess.run(
                cmd,
                cwd=self._worktree_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                input=stdin_input,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConflictError(f"Claude timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            raise ConflictError(
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

    def _commit_and_push(self, branch: str, resolved_files: list[str]) -> None:
        """Stage resolved files, commit the merge, and push."""
        for file_path in resolved_files:
            proc = subprocess.run(
                ["git", "add", file_path],
                cwd=self._worktree_path,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise ConflictError(
                    f"git add failed for {file_path} (code {proc.returncode}): {proc.stderr[:300]}"
                )

        proc = subprocess.run(
            ["git", "commit", "-m", "[agent] fix: resolve merge conflicts"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConflictError(
                f"git commit failed (code {proc.returncode}): {proc.stderr[:300]}"
            )
        logger.info("Committed resolved conflicts")

        proc = subprocess.run(
            ["git", "push", "origin", f"HEAD:{branch}"],
            cwd=self._worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ConflictError(
                f"git push failed (code {proc.returncode}): {proc.stderr[:500]}"
            )
        logger.info("Pushed resolved conflicts to %s", branch)

    def _post_comment(self, resolved_files: list[str]) -> None:
        files_list = "\n".join(f"- `{f}`" for f in resolved_files)
        body = (
            f"## \U0001f527 Merge Conflicts Resolved\n\n"
            f"The following files had merge conflicts that were automatically resolved:\n\n"
            f"{files_list}\n\n"
            f"*Automated conflict resolution by agent-shop conflict resolver*"
        )
        try:
            proc = subprocess.run(
                ["gh", "pr", "comment", str(self.pr_number), "--body", body],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            # posting the comment is non-critical; a timeout is not fatal
            logger.warning(
                "gh pr comment timed out after %ds on PR #%d",
                self.timeout,
                self.pr_number,
            )
            return
        if proc.returncode != 0:
            logger.warning(
                "gh pr comment failed (code %d): %s",
                proc.returncode,
                proc.stderr[:300],
            )
        else:
            logger.info(
                "Posted conflict resolution comment on PR #%d", self.pr_number
            )

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

    def _run_gh(self, args: list[str]) -> str:
        cmd = ["gh"] + args
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConflictError(
                f"gh {' '.join(args)} timed out after {self.timeout}s"
            ) from exc
        if proc.returncode != 0:
            raise ConflictError(
                f"gh {' '.join(args)} failed (code {proc.returncode}): {proc.stderr[:500]}"
            )
        return proc.stdout


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Conflict resolution agent — resolves merge conflicts in a PR"
    )
    parser.add_argument("--pr", type=int, required=True, help="PR number to resolve")
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

    resolver = ConflictResolver(
        repo_path=args.repo_path,
        pr_number=args.pr,
        model=args.model,
        timeout=args.timeout,
    )

    result = resolver.resolve()

    if result.success:
        if result.resolved_files:
            print(f"\nPR #{result.pr_number} — conflicts resolved successfully")
            print(f"Resolved files ({len(result.resolved_files)}):")
            for f in result.resolved_files:
                print(f"  - {f}")
        else:
            print(f"\nPR #{result.pr_number} — no conflicts found")
    else:
        print(f"\nPR #{result.pr_number} — conflict resolution FAILED")
        print(f"Error: {result.error}")
        raise SystemExit(1)
