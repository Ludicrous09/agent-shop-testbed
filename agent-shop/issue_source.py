"""Read GitHub Issues as a task source for the agent-shop orchestrator.

Issue body format
-----------------
The following sections are all optional except for the description itself::

    Description of the task...

    Files:
    - src/foo.py
    - tests/test_foo.py

    Depends on: #1, #2
    Max turns: 40

Sections are parsed case-insensitively.  The ``Files:`` block is a
bullet list (``-`` or ``*`` prefix).  ``Depends on:`` accepts a
comma-separated list of ``#N`` references.  ``Max turns:`` must be a
plain integer.

Labels of the form ``priority:N`` (e.g. ``priority:1``) set the task
priority; the default is 1.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worker import Task

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_FILES_HEADER = re.compile(r"^\s*(?:#{1,3}\s+)?files\s*:?\s*$", re.IGNORECASE | re.MULTILINE)
_FILES_ITEM = re.compile(r"^\s*[-*]\s+(.+)$", re.MULTILINE)
_DEPENDS_ON = re.compile(r"(?:#{1,3}\s+)?depends\s+on\s*:?\s*(.+)", re.IGNORECASE)
_DEPENDS_REF = re.compile(r"#(\d+)")
_MAX_TURNS = re.compile(r"(?:#{1,3}\s+)?max\s+turns\s*:?\s*(\d+)", re.IGNORECASE)
_PRIORITY_LABEL = re.compile(r"^priority:(\d+)$", re.IGNORECASE)

# Matches known source/config file extensions used to distinguish file paths
# from Python dotted identifiers (e.g. discord.Embed, self.active_tasks).
_KNOWN_FILE_EXT = re.compile(
    r"\.(py|js|ts|jsx|tsx|json|yaml|yml|toml|md|txt|sh|cfg|ini|html|css|sql|"
    r"go|rs|java|c|cpp|h|rb|php|env|lock|tf|proto|graphql|vue|svelte)$",
    re.IGNORECASE,
)


def _is_file_path(entry: str) -> bool:
    """Return True if *entry* looks like a file path rather than a Python symbol.

    Filters out dotted Python identifiers such as ``self.active_tasks``,
    ``discord.Embed``, and ``discord.utils.format_dt`` that sometimes appear
    in issue "Files:" sections alongside real paths.

    A string is treated as a file path when it either:
    - contains a ``/`` (path separator), or
    - ends with a recognised source/config file extension.
    """
    if not entry:
        return False
    if "/" in entry:
        return True
    return bool(_KNOWN_FILE_EXT.search(entry))


def _parse_files(body: str) -> list[str]:
    """Return the file list from a 'Files:' bullet section, or [].

    Non-path entries (Python symbols, dotted identifiers, etc.) are silently
    dropped so that only genuine file paths reach the allowlist enforcer.
    """
    match = _FILES_HEADER.search(body)
    if not match:
        return []
    # Grab everything after the 'Files:' header up to the next blank line
    # or next section header (a line that is not a bullet and not blank).
    after = body[match.end():]
    lines: list[str] = []
    found_item = False
    for line in after.splitlines():
        if not line.strip():
            if found_item:
                break  # blank line after items = end of section
            continue  # skip blank lines before first item
        m = _FILES_ITEM.match(line)
        if m:
            lines.append(m.group(1).strip())
            found_item = True
        else:
            break
    return [entry for entry in lines if _is_file_path(entry)]


def _parse_depends_on(body: str, all_numbers: set[int]) -> list[str]:
    """Return depends_on as ['issue-N', ...], only for IDs in all_numbers."""
    match = _DEPENDS_ON.search(body)
    if not match:
        return []
    refs = _DEPENDS_REF.findall(match.group(1))
    return [f"issue-{n}" for n in refs if int(n) in all_numbers]


def _parse_max_turns(body: str) -> int:
    match = _MAX_TURNS.search(body)
    if match:
        return int(match.group(1))
    return 50


def _parse_priority(labels: list[dict]) -> int:
    for lbl in labels:
        name = lbl.get("name", "")
        m = _PRIORITY_LABEL.match(name)
        if m:
            return int(m.group(1))
    return 1


def _issue_to_task(issue: dict, all_numbers: set[int]) -> Task:
    number: int = issue["number"]
    body: str = issue.get("body") or ""
    labels: list[dict] = issue.get("labels") or []

    return Task(
        id=f"issue-{number}",
        title=issue["title"],
        description=body,
        files_touched=_parse_files(body),
        depends_on=_parse_depends_on(body, all_numbers),
        priority=_parse_priority(labels),
        max_turns=_parse_max_turns(body),
        model="sonnet",
    )


# ---------------------------------------------------------------------------
# IssueSource
# ---------------------------------------------------------------------------

class IssueSource:
    """Fetch tasks from GitHub Issues labelled with *label*.

    Parameters
    ----------
    repo_path:
        Path to the local git repository (used as cwd for ``gh`` calls).
    label:
        GitHub label that marks issues as ready for the agent.
    """

    def __init__(self, repo_path: str | Path = ".", label: str = "agent-ready") -> None:
        self.repo_path = Path(repo_path).resolve()
        self.label = label

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_tasks(self) -> list[Task]:
        """Fetch open issues with the configured label and return Tasks.

        Before adding each issue as a task, checks whether a merged PR already
        references it.  If so, the issue is auto-closed (with a comment) and
        the task is skipped to prevent duplicate work.

        Dependencies that reference issue numbers not in the fetched set are
        silently dropped.  Results are sorted by priority (lowest = highest).
        """
        issues = self._gh_list_issues()
        # Filter out issues already resolved by merged PRs first, so that
        # all_numbers only contains issues that will actually become tasks.
        # This prevents depends_on from referencing tasks that don't exist.
        accepted: list[dict] = []
        for iss in issues:
            number: int = iss["number"]
            pr_number = self._find_merged_pr(number)
            if pr_number is not None:
                log.info("Skipping issue #%d — already resolved by PR #%d", number, pr_number)
                try:
                    self._close_already_resolved(number, pr_number)
                except RuntimeError as exc:
                    log.warning("Failed to close already-resolved issue #%d: %s", number, exc)
            else:
                accepted.append(iss)
        all_numbers: set[int] = {iss["number"] for iss in accepted}
        tasks = [_issue_to_task(iss, all_numbers) for iss in accepted]
        tasks.sort(key=lambda t: t.priority)
        return tasks

    def mark_complete(self, task_id: str, pr_url: str) -> None:
        """Post a completion comment and close the issue."""
        number = self._number_from_id(task_id)
        self._gh_comment(number, f"Completed. PR: {pr_url}")
        self._gh(["issue", "close", str(number)], timeout=120)

    def mark_failed(self, task_id: str, error: str) -> None:
        """Post an error comment and add the 'agent-failed' label.

        The issue is intentionally left open so it can be retried.
        """
        number = self._number_from_id(task_id)
        self._gh_comment(number, f"Agent failed.\n\n```\n{error}\n```")
        self._gh(["issue", "edit", str(number), "--add-label", "agent-failed"], timeout=120)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _gh(self, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
        """Run a ``gh`` subcommand, raising on non-zero exit."""
        cmd = ["gh"] + args
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"gh command timed out after {timeout}s: {' '.join(cmd)}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"gh command failed ({result.returncode}): "
                f"{' '.join(cmd)}\n{result.stderr.strip()}"
            )
        return result

    def _gh_list_issues(self) -> list[dict]:
        result = self._gh([
            "issue", "list",
            "--label", self.label,
            "--state", "open",
            "--json", "number,title,body,labels",
        ])
        return json.loads(result.stdout)

    def _find_merged_pr(self, number: int) -> int | None:
        """Return the PR number of a merged PR whose title references this issue, or None.

        Uses ``gh pr list --state merged --search 'issue-{number} in:title'``.
        Returns ``None`` if no such PR is found or if the gh call fails.
        """
        try:
            result = self._gh([
                "pr", "list",
                "--state", "merged",
                "--search", f"issue-{number} in:title",
                "--limit", "5",
                "--json", "number,state,title",
            ])
        except RuntimeError as exc:
            log.warning("_find_merged_pr gh call failed for issue #%d: %s", number, exc)
            return None
        try:
            prs = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        # GitHub's search is a substring match, so 'issue-1' can return PRs
        # titled 'Fix issue-10', 'Fix issue-100', etc.  Validate that the
        # title contains 'issue-{number}' as a whole token before accepting.
        pattern = re.compile(rf"\bissue-{number}\b", re.IGNORECASE)
        for pr in prs:
            # --state merged guarantees every returned PR is merged; kept as a
            # defensive guard in case the API behaviour ever changes.
            if pr.get("state") == "MERGED" and pattern.search(pr.get("title", "")):
                return int(pr["number"])
        return None

    def _close_already_resolved(self, number: int, pr_number: int) -> None:
        """Comment on, remove the agent-ready label from, and close an issue."""
        self._gh_comment(number, f"Issue already resolved by merged PR #{pr_number}")
        self._gh(["issue", "edit", str(number), "--remove-label", self.label])
        self._gh(["issue", "close", str(number)])

    def _gh_comment(self, number: int, body: str) -> None:
        self._gh(["issue", "comment", str(number), "--body", body], timeout=120)

    @staticmethod
    def _number_from_id(task_id: str) -> int:
        """Extract the issue number from a task id like 'issue-42'."""
        parts = task_id.split("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            raise ValueError(f"Invalid task_id format: {task_id!r}")
        return int(parts[1])


# ---------------------------------------------------------------------------
# __main__ — list available issues
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="List GitHub Issues available as tasks")
    parser.add_argument("--repo-path", default=".", help="Path to the git repo (default: cwd)")
    parser.add_argument("--label", default="agent-ready", help="Issue label to filter on")
    args = parser.parse_args()

    source = IssueSource(repo_path=args.repo_path, label=args.label)
    try:
        tasks = source.fetch_tasks()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not tasks:
        print(f"No open issues labelled '{args.label}'.")
        sys.exit(0)

    print(f"{'ID':<14} {'Pri':>3}  {'Max':>3}  {'Deps':<20}  Title")
    print("-" * 72)
    for t in tasks:
        deps = ", ".join(t.depends_on) if t.depends_on else "—"
        print(f"{t.id:<14} {t.priority:>3}  {t.max_turns:>3}  {deps:<20}  {t.title}")
