"""Code review agent module — runs Claude to review a GitHub PR and posts results."""

import argparse
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = {"error", "warning", "suggestion"}
_LARGE_PROMPT_THRESHOLD = 100 * 1024  # 100 KB

REVIEW_PROMPT_TEMPLATE = """\
You are a thorough but fair code reviewer. Review the following pull request and respond with ONLY a JSON object (no markdown fences, no prose before or after).

## PR Diff

{diff}

## Changed Files (full content)

{file_contents}

## Review Instructions

Your default verdict is APPROVE. Only use `request_changes` when there is a genuine blocking problem.

### Severity levels — use exactly one per comment:

- **error** — A blocking problem that MUST be fixed before merging. Reserve this for:
  - Actual bugs or logic errors that would cause incorrect behaviour
  - Missing error handling for realistic, likely failure modes
  - Security issues (injection, path traversal, secret exposure, etc.)
  - Missing test coverage for newly added functions or logic branches
  - Incorrect type hints that would cause runtime failures

- **warning** — A non-blocking issue worth fixing but not blocking. Use for:
  - Incorrect or missing type hints that are merely imprecise (not crash-causing)
  - Potential edge cases that are unlikely but worth considering
  - Moderate design concerns that have a clear improvement path

- **suggestion** — A minor improvement or preference. Use for:
  - Code style preferences (naming, formatting, docstring style)
  - Readability tweaks
  - Optional refactoring ideas
  - Any nitpick that a reasonable developer might disagree with

### Verdict rules:
- Set verdict to `request_changes` ONLY if at least one comment has severity `error`.
- Set verdict to `approve` if all comments are `warning` or `suggestion` (or there are no comments).
- Do NOT use `request_changes` for style, naming, or minor improvements — always `approve` with `suggestion` comments instead.

Respond with exactly this JSON structure:
{{
  "verdict": "approve" | "request_changes",
  "summary": "Overall assessment of the PR in 2-4 sentences.",
  "comments": [
    {{
      "file": "relative/path/to/file.py",
      "line": <line number as integer>,
      "severity": "error" | "warning" | "suggestion",
      "comment": "What is wrong and how to fix it."
    }}
  ]
}}

If there are no issues, return an empty comments array and verdict "approve".
"""


@dataclass
class ReviewComment:
    file: str
    line: int
    severity: str
    comment: str


@dataclass
class ReviewResult:
    verdict: str
    summary: str
    comments: list[ReviewComment]
    pr_number: int
    reviewed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ReviewError(Exception):
    """Raised when the review agent encounters an unrecoverable error."""


class ReviewAgent:
    def __init__(
        self,
        repo_path: str | Path,
        pr_number: int,
        model: str = "sonnet",
        timeout: int = 300,
        gh_timeout: int = 60,
    ):
        self.repo_path = Path(repo_path).resolve()
        self.pr_number = pr_number
        self.model = model
        self.timeout = timeout
        self.gh_timeout = gh_timeout
        self._owner, self._repo = self._parse_remote()
        logger.info(
            "ReviewAgent initialised for PR #%d in %s/%s (model=%s)",
            pr_number,
            self._owner,
            self._repo,
            model,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def review(self) -> ReviewResult:
        logger.info("Starting review of PR #%d", self.pr_number)

        diff = self._get_diff()
        logger.info("Got diff (%d chars)", len(diff))

        pr_data = self._fetch_pr_data()
        files = self._get_changed_files(pr_data)
        logger.info("Changed files: %s", files)

        head_branch = self._get_head_branch(pr_data)
        logger.info("PR head branch: %s", head_branch)

        self._fetch_remote()

        file_contents = self._collect_file_contents(files, head_branch)

        prompt = self._build_prompt(diff, file_contents)
        logger.info("Built review prompt (%d chars)", len(prompt))

        raw_json = self._run_claude(prompt)
        logger.info("Claude returned review JSON (%d chars)", len(raw_json))

        review_data = self._parse_review(raw_json)

        result = ReviewResult(
            verdict=review_data["verdict"],
            summary=review_data["summary"],
            comments=[
                ReviewComment(
                    file=c["file"],
                    line=c["line"],
                    severity=c["severity"],
                    comment=c["comment"],
                )
                for c in review_data.get("comments", [])
            ],
            pr_number=self.pr_number,
        )

        self._post_review(result)
        logger.info(
            "Review posted: verdict=%s, comments=%d",
            result.verdict,
            len(result.comments),
        )
        return result

    # ------------------------------------------------------------------
    # Step implementations
    # ------------------------------------------------------------------

    def _get_diff(self) -> str:
        result = self._run_gh(["pr", "diff", str(self.pr_number)])
        if not result.strip():
            raise ReviewError(f"PR #{self.pr_number} has an empty diff")
        return result

    def _fetch_pr_data(self) -> dict:
        """Fetch all required PR fields in a single gh pr view call."""
        output = self._run_gh(
            ["pr", "view", str(self.pr_number), "--json", "files,headRefName"]
        )
        return json.loads(output)

    def _get_changed_files(self, pr_data: dict) -> list[str]:
        files = [f["path"] for f in pr_data.get("files", [])]
        if not files:
            raise ReviewError(f"PR #{self.pr_number} has no changed files")
        return files

    def _get_head_branch(self, pr_data: dict) -> str:
        return pr_data["headRefName"]

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

    def _collect_file_contents(
        self, files: list[str], head_branch: str
    ) -> dict[str, str]:
        contents: dict[str, str] = {}
        ref = f"origin/{head_branch}"
        for path in files:
            logger.info("Fetching content of %s from %s", path, ref)
            proc = subprocess.run(
                ["git", "show", f"{ref}:{path}"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Could not read %s from %s: %s", path, ref, proc.stderr[:200]
                )
                contents[path] = f"<file not readable: {proc.stderr.strip()[:100]}>"
            else:
                contents[path] = proc.stdout
        return contents

    def _build_prompt(self, diff: str, file_contents: dict[str, str]) -> str:
        fc_sections = []
        for path, content in file_contents.items():
            fc_sections.append(f"### {path}\n\n```\n{content}\n```")
        file_contents_str = "\n\n".join(fc_sections) if fc_sections else "(none)"
        return REVIEW_PROMPT_TEMPLATE.format(
            diff=diff,
            file_contents=file_contents_str,
        )

    def _run_claude(self, prompt: str) -> str:
        prompt_bytes = prompt.encode("utf-8")
        use_stdin = len(prompt_bytes) > _LARGE_PROMPT_THRESHOLD
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
            logger.info(
                "Prompt size %d bytes exceeds threshold; passing via stdin",
                len(prompt_bytes),
            )
        else:
            cmd += ["-p", prompt]
            stdin_input = None
        logger.info("Running claude (timeout=%ds)", self.timeout)
        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                input=stdin_input,
            )
        except subprocess.TimeoutExpired as e:
            raise ReviewError(f"Claude timed out after {self.timeout}s") from e

        if proc.returncode != 0:
            raise ReviewError(
                f"Claude exited with code {proc.returncode}: {proc.stderr[:500]}"
            )

        # claude --output-format json wraps the response in an envelope
        try:
            envelope = json.loads(proc.stdout)
            cost = envelope.get("cost_usd", "unknown")
            turns = envelope.get("num_turns", "unknown")
            logger.info("Claude cost=$%s turns=%s", cost, turns)
            text = envelope.get("result", proc.stdout)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Could not parse claude envelope JSON; using raw output")
            text = proc.stdout

        return text

    def _parse_review(self, text: str) -> dict:
        # Strip optional markdown code fences
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to extract the first {...} block
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise ReviewError(
                    f"Could not find JSON in Claude response: {cleaned[:300]}"
                )
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError as e:
                raise ReviewError(f"Failed to parse review JSON: {e}") from e

        verdict = data.get("verdict", "")
        if verdict not in ("approve", "request_changes"):
            raise ReviewError(f"Unexpected verdict value: {verdict!r}")

        # Enforce threshold: REQUEST_CHANGES only if at least one comment is severity "error".
        # This overrides the model's verdict to prevent false positives from style suggestions.
        comments = data.get("comments", [])
        for c in comments:
            if c.get("severity") not in _VALID_SEVERITIES:
                logger.warning(
                    "Invalid severity %r in review comment — defaulting to 'suggestion'",
                    c.get("severity"),
                )
                c["severity"] = "suggestion"
        has_error = any(c.get("severity") == "error" for c in comments)
        if verdict == "request_changes" and not has_error:
            logger.info(
                "Overriding verdict from request_changes to approve — no error-severity comments found"
            )
            data["verdict"] = "approve"

        return data

    def _post_review(self, result: ReviewResult) -> None:
        event = "APPROVE" if result.verdict == "approve" else "REQUEST_CHANGES"

        comments = []
        for c in result.comments:
            comments.append(
                {
                    "path": c.file,
                    "line": c.line,
                    "body": f"**[{c.severity.upper()}]** {c.comment}",
                    "side": "RIGHT",
                }
            )

        logger.info(
            "Posting review to PR #%d (event=%s, inline_comments=%d)",
            self.pr_number,
            event,
            len(comments),
        )

        # Build the full review body with inline comments included as text
        comment_text = ""
        if result.comments:
            comment_lines = []
            for c in result.comments:
                comment_lines.append(
                    f"- **[{c.severity.upper()}]** `{c.file}:{c.line}` — {c.comment}"
                )
            comment_text = "\n\n### Inline Comments\n\n" + "\n".join(comment_lines)

        full_body = result.summary + comment_text

        # Post as a PR comment with verdict tag (avoids "can't approve own PR" issue)
        verdict_tag = f"[REVIEW: {event}]"
        full_body = f"## 🤖 Agent Review\n\n{verdict_tag}\n\n{full_body}"

        try:
            proc = subprocess.run(
                ["gh", "pr", "comment", str(self.pr_number), "--body", full_body],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=self.gh_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReviewError(
                f"gh pr comment timed out after {self.gh_timeout}s"
            ) from exc
        if proc.returncode != 0:
            raise ReviewError(
                f"gh pr comment failed (code {proc.returncode}): {proc.stderr[:500]}"
            )
        logger.info("Review posted successfully as PR comment")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run_gh(self, args: list[str]) -> str:
        cmd = ["gh"] + args
        delays = [5, 15, 30]
        for attempt in range(3):
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=self.repo_path,
                    capture_output=True,
                    text=True,
                    timeout=self.gh_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise ReviewError(
                    f"gh {' '.join(args)} timed out after {self.gh_timeout}s"
                ) from exc
            if proc.returncode == 0:
                return proc.stdout
            if attempt < 2 and (
                "rate limit" in proc.stderr.lower() or "429" in proc.stderr
            ):
                logger.warning(
                    "gh rate limit hit, retrying in %ds (attempt %d/3)",
                    delays[attempt],
                    attempt + 1,
                )
                time.sleep(delays[attempt])
                continue
            raise ReviewError(
                f"gh {' '.join(args)} failed (code {proc.returncode}): {proc.stderr[:500]}"
            )
        raise ReviewError(f"gh {' '.join(args)} failed after retries")

    def _parse_remote(self) -> tuple[str, str]:
        proc = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=self.repo_path if self.repo_path.exists() else Path("."),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise ReviewError(f"Could not get git remote URL: {proc.stderr[:200]}")

        url = proc.stdout.strip()
        # SSH: git@github.com:owner/repo.git
        # HTTPS: https://github.com/owner/repo.git
        match = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?$", url)
        if not match:
            raise ReviewError(f"Could not parse owner/repo from remote URL: {url!r}")
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

    parser = argparse.ArgumentParser(description="Code review agent")
    parser.add_argument("--pr", type=int, required=True, help="PR number to review")
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

    agent = ReviewAgent(
        repo_path=args.repo_path,
        pr_number=args.pr,
        model=args.model,
        timeout=args.timeout,
    )

    result = agent.review()

    print(
        f"\nPR #{result.pr_number} Review — {result.reviewed_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    print(f"Verdict : {result.verdict.upper()}")
    print(f"Summary : {result.summary}")
    if result.comments:
        print(f"\nComments ({len(result.comments)}):")
        for c in result.comments:
            print(f"  [{c.severity.upper()}] {c.file}:{c.line} — {c.comment}")
    else:
        print("\nNo inline comments.")
