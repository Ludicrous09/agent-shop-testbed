"""Architect agent that uses Claude Opus to design solutions before workers execute."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("architect")

_LARGE_PROMPT_THRESHOLD = 100 * 1024  # 100 KB


_SYSTEM_PROMPT = """\
You are a senior software architect reviewing a GitHub issue and designing a \
detailed implementation plan for an autonomous coding agent to execute.

Analyze the issue, the existing codebase structure, and the relevant code files. \
Then produce a comprehensive implementation spec covering:

1. **Summary** — What needs to change and why
2. **Exact function signatures** — With type hints and docstrings for all \
new/modified functions
3. **Error handling requirements** — What errors to catch and how to handle them
4. **Test cases** — Specific test names and what each verifies
5. **Acceptance criteria** — How to verify the implementation is correct
6. **Existing patterns to follow** — Code style, patterns, and conventions \
observed in the codebase
7. **Potential risks or gotchas** — Edge cases, race conditions, breaking changes

Be precise and actionable. The worker agent will implement this spec without \
asking clarifying questions.
"""

_USER_TEMPLATE = """\
## Issue #{number}: {title}

{body}

---

## Repository File Tree

```
{file_tree}
```

{files_content}
---

Please design a detailed implementation plan for this issue.\
"""

_FILES_HEADER = re.compile(
    r"^\s*(?:#{1,3}\s+)?files\s*:?\s*$", re.IGNORECASE | re.MULTILINE
)
_FILES_ITEM = re.compile(r"^\s*[-*]\s+(.+)$", re.MULTILINE)


def _parse_files_from_body(body: str) -> list[str]:
    """Extract the file list from a 'Files:' bullet section in the issue body.

    Returns an empty list when no Files section is found.
    """
    match = _FILES_HEADER.search(body)
    if not match:
        return []
    after = body[match.end():]
    lines: list[str] = []
    found_item = False
    for line in after.splitlines():
        if not line.strip():
            if found_item:
                break
            continue
        m = _FILES_ITEM.match(line)
        if m:
            lines.append(m.group(1).strip())
            found_item = True
        else:
            break
    return lines


class ArchitectAgent:
    """Design a detailed implementation spec for a GitHub issue using Claude Opus.

    Parameters
    ----------
    issue_number:
        The GitHub issue number to analyze.
    repo_path:
        Path to the local git repository (used as cwd for ``gh`` calls and
        for reading source files).
    """

    def __init__(
        self,
        issue_number: int,
        repo_path: str | Path = ".",
    ) -> None:
        self.issue_number = issue_number
        self.repo_path = Path(repo_path).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def design(self) -> str:
        """Run the full architect pipeline and return a structured markdown spec.

        Steps
        -----
        1. Fetch the issue body from GitHub.
        2. Gather codebase context (file tree, relevant file contents, CLAUDE.md).
        3. Send everything to Claude Opus for a detailed implementation plan.

        Returns
        -------
        str
            A structured markdown implementation spec.
        """
        log.info("Fetching issue #%d", self.issue_number)
        issue = self._fetch_issue()

        log.info("Gathering codebase context for issue #%d", self.issue_number)
        file_tree = self._get_file_tree()
        files_content = self._get_relevant_files(issue.get("body") or "")

        log.info(
            "Sending issue #%d to Claude Opus for architecture design",
            self.issue_number,
        )
        spec = self._call_claude(issue, file_tree, files_content)
        log.info(
            "Architect spec generated for issue #%d (%d chars)",
            self.issue_number,
            len(spec),
        )
        return spec

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fetch_issue(self) -> dict:
        """Return issue metadata dict from the GitHub API."""
        result = self._gh([
            "issue", "view", str(self.issue_number),
            "--json", "number,title,body,labels",
        ])
        return json.loads(result.stdout)

    def _get_file_tree(self) -> str:
        """Return a newline-separated listing of all git-tracked files.

        Falls back to an explanatory string on error.
        """
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            result = subprocess.run(
                ["git", "ls-files"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            log.warning("git ls-files timed out after 30s")
            return "(could not list files)"
        if result.returncode != 0:
            log.warning("git ls-files failed: %s", result.stderr[:200])
            return "(could not list files)"
        files = [f for f in result.stdout.strip().splitlines() if f]
        return "\n".join(files)

    _MAX_FILE_BYTES: int = 50 * 1024  # 50 KB — files larger than this are skipped

    def _get_relevant_files(self, body: str) -> str:
        """Build a markdown context section with contents of relevant files.

        Reads:
        - CLAUDE.md if it exists
        - Files listed in the issue body's Files section
        - Files that import or are imported by the listed files
        """
        sections: list[str] = []

        # Always include CLAUDE.md if it exists
        claude_md = self.repo_path / "CLAUDE.md"
        if claude_md.exists():
            try:
                content = claude_md.read_text(encoding="utf-8")
                sections.append(f"## CLAUDE.md\n\n```\n{content}\n```")
            except OSError as exc:
                log.warning("Could not read CLAUDE.md: %s", exc)

        # Collect files listed in the issue body plus their import-related files
        issue_files = _parse_files_from_body(body)
        all_files: set[str] = set(issue_files)
        for filepath in issue_files:
            all_files.update(self._find_related_files(filepath))

        # Read each file's content
        for filepath in sorted(all_files):
            full_path = self.repo_path / filepath

            # Guard against path traversal: ensure the resolved path stays inside the repo
            try:
                resolved = full_path.resolve()
            except OSError as exc:
                log.warning("Could not resolve path %s: %s", filepath, exc)
                continue
            if not resolved.is_relative_to(self.repo_path):
                log.warning(
                    "Skipping file outside repo directory: %s", filepath
                )
                continue

            if not full_path.exists():
                log.warning("File not found: %s", filepath)
                continue

            # Skip files that are too large to avoid blowing up the Claude context
            try:
                file_size = full_path.stat().st_size
            except OSError as exc:
                log.warning("Could not stat %s: %s", filepath, exc)
                continue
            if file_size > self._MAX_FILE_BYTES:
                log.warning(
                    "Skipping large file %s (%d bytes > %d byte limit)",
                    filepath,
                    file_size,
                    self._MAX_FILE_BYTES,
                )
                continue

            try:
                content = full_path.read_text(encoding="utf-8")
                ext = Path(filepath).suffix
                lang = "python" if ext == ".py" else ext.lstrip(".") or "text"
                sections.append(f"## {filepath}\n\n```{lang}\n{content}\n```")
            except OSError as exc:
                log.warning("Could not read %s: %s", filepath, exc)

        if not sections:
            return ""

        return "## Relevant Files\n\n" + "\n\n".join(sections) + "\n\n"

    def _find_related_files(self, filepath: str) -> list[str]:
        """Find local Python files imported by *filepath*.

        Scans ``filepath`` for ``import X`` / ``from X import`` statements and
        returns paths for modules that exist as ``.py`` files in the repo.

        Note: only one level deep — transitive imports are not resolved.
        """
        related: list[str] = []
        full_path = self.repo_path / filepath

        if not full_path.exists() or not filepath.endswith(".py"):
            return related

        try:
            content = full_path.read_text(encoding="utf-8")
        except OSError:
            return related

        import_pattern = re.compile(
            r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))",
            re.MULTILINE,
        )
        for match in import_pattern.finditer(content):
            module = match.group(1) or match.group(2)
            if not module:
                continue
            module_file = module.replace(".", "/") + ".py"
            if (self.repo_path / module_file).exists():
                related.append(module_file)

        return related

    def _call_claude(self, issue: dict, file_tree: str, files_content: str) -> str:
        """Send the issue and codebase context to Claude Opus via CLI and return the spec.

        Parameters
        ----------
        issue:
            Issue metadata dict with ``number``, ``title``, and ``body`` keys.
        file_tree:
            Newline-separated list of tracked files.
        files_content:
            Markdown string with contents of relevant files.

        Returns
        -------
        str
            The raw markdown spec text from Claude.

        Raises
        ------
        RuntimeError
            When the claude CLI exits with a non-zero code or returns no text.
        """
        def _esc(s: str) -> str:
            """Escape braces in user-controlled strings so str.format() won't choke."""
            return s.replace("{", "{{").replace("}", "}}")

        user_msg = _USER_TEMPLATE.format(
            number=issue["number"],
            title=_esc(issue["title"]),
            body=_esc(issue.get("body") or "(no description provided)"),
            file_tree=_esc(file_tree),
            files_content=_esc(files_content),
        )

        full_prompt = _SYSTEM_PROMPT + "\n\n" + user_msg

        use_stdin = len(full_prompt.encode("utf-8")) > _LARGE_PROMPT_THRESHOLD
        if use_stdin:
            cmd = [
                "claude",
                "--output-format", "json",
                "--model", "claude-opus-4-6",
                "--dangerously-skip-permissions",
            ]
            stdin_input: str | None = full_prompt
        else:
            cmd = [
                "claude",
                "-p", full_prompt,
                "--output-format", "json",
                "--model", "claude-opus-4-6",
                "--dangerously-skip-permissions",
            ]
            stdin_input = None

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                env=env,
                input=stdin_input,
                timeout=600,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "claude command timed out after 600s"
            ) from exc

        if result.returncode != 0:
            raise RuntimeError(
                f"claude command failed ({result.returncode}): {result.stderr[:500]}"
            )

        # Parse the JSON envelope produced by claude --output-format json
        try:
            envelope = json.loads(result.stdout)
            text = envelope.get("result", result.stdout)
        except (json.JSONDecodeError, TypeError):
            log.warning("Could not parse claude envelope JSON; using raw output")
            text = result.stdout

        if not text or not text.strip():
            raise RuntimeError("Claude response contained no text")

        return text.strip()

    def _gh(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run a ``gh`` subcommand, raising ``RuntimeError`` on non-zero exit."""
        cmd = ["gh"] + args
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"gh command timed out after 60s: {' '.join(cmd)}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"gh command failed ({result.returncode}): "
                f"{' '.join(cmd)}\n{result.stderr.strip()}"
            )
        return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Design an implementation spec for a GitHub issue using Claude Opus"
    )
    parser.add_argument(
        "--issue",
        type=int,
        required=True,
        help="GitHub issue number to analyze",
    )
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Path to the git repo (default: cwd)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    agent = ArchitectAgent(issue_number=args.issue, repo_path=args.repo_path)
    try:
        spec = agent.design()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(spec)


if __name__ == "__main__":
    main()
