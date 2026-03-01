"""Decomposition agent that breaks vague GitHub issues into well-scoped sub-tasks."""

from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

log = logging.getLogger("decomposer")


_SYSTEM_PROMPT = """\
You are a software architect that decomposes high-level or vague GitHub issues
into well-scoped, independently executable sub-tasks for autonomous agent workers.

Rules:
- Make tasks as independent as possible; minimise cross-task dependencies.
- Minimise file overlap between tasks so workers can run in parallel.
- Include test files for every source file created or modified.
- Set realistic max_turns based on complexity:
    simple (1-2 functions / minor changes): 15-25
    medium (new module or significant feature): 30-50
    complex (architectural work, multiple modules): 55-80
- Use depends_on only when one task genuinely requires another's output.
- Descriptions must be specific enough for a worker to implement without
  further clarification — include exact function signatures, class names,
  expected behaviour, and test patterns where applicable.

Respond with a single JSON object (no markdown fences) with this schema:
{
  "tasks": [
    {
      "id": "<short-slug>",
      "title": "<action-oriented title>",
      "description": "<detailed implementation instructions>",
      "files_touched": ["<path/to/file.py>", ...],
      "depends_on": ["<other-task-id>", ...],
      "priority": <1-5 integer, 1 = highest>,
      "max_turns": <integer>
    },
    ...
  ]
}
"""

_USER_TEMPLATE = """\
Decompose the following GitHub issue into independent, parallelisable sub-tasks.

Issue #{number}: {title}

{body}
"""


@dataclass
class DecomposedIssue:
    """Result of a successful decomposition."""

    parent_issue_number: int
    sub_issue_numbers: list[int] = field(default_factory=list)


@dataclass
class SubTask:
    """A single decomposed sub-task."""

    id: str
    title: str
    description: str
    files_touched: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    priority: int = 1
    max_turns: int = 30


class DecomposerAgent:
    """Decompose a vague GitHub issue into well-scoped agent-ready sub-issues.

    Parameters
    ----------
    issue_number:
        The GitHub issue number to decompose.
    repo_path:
        Path to the local git repository (used as cwd for ``gh`` calls).
    """

    DECOMPOSED_LABEL = "agent-decomposed"
    READY_LABEL = "agent-ready"

    def __init__(
        self,
        issue_number: int,
        repo_path: str | Path = ".",
    ) -> None:
        self.issue_number = issue_number
        self.repo_path = Path(repo_path).resolve()
        self._client = anthropic.Anthropic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(self) -> DecomposedIssue:
        """Run the full decomposition pipeline.

        1. Fetch the issue body from GitHub.
        2. Send it to Claude and receive structured sub-tasks.
        3. Create a new GitHub issue for each sub-task.
        4. Comment on the original issue and relabel it.

        Returns a :class:`DecomposedIssue` with the parent and sub-issue numbers.
        """
        log.info("Fetching issue #%d", self.issue_number)
        issue = self._fetch_issue()

        log.info("Sending issue #%d to Claude for decomposition", self.issue_number)
        sub_tasks = self._call_claude(issue)
        log.info("Claude returned %d sub-tasks", len(sub_tasks))

        log.info("Ensuring label '%s' exists", self.DECOMPOSED_LABEL)
        self._ensure_decomposed_label()

        log.info("Creating %d sub-issues", len(sub_tasks))
        sub_numbers = self._create_sub_issues(sub_tasks)

        if not sub_numbers:
            log.error(
                "All sub-issue creations failed for issue #%d — not relabelling",
                self.issue_number,
            )
            return DecomposedIssue(
                parent_issue_number=self.issue_number,
                sub_issue_numbers=[],
            )

        log.info("Finalising parent issue #%d", self.issue_number)
        self._finalize_parent_issue(sub_numbers)

        return DecomposedIssue(
            parent_issue_number=self.issue_number,
            sub_issue_numbers=sub_numbers,
        )

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

    def _call_claude(self, issue: dict) -> list[SubTask]:
        """Send the issue to Claude and return parsed sub-tasks."""
        user_msg = _USER_TEMPLATE.format(
            number=issue["number"],
            title=issue["title"],
            body=issue.get("body") or "(no description provided)",
        )

        with self._client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        ) as stream:
            response = stream.get_final_message()

        # Extract the text block (thinking blocks are separate)
        text_block = next(
            (b for b in response.content if b.type == "text"),
            None,
        )
        if text_block is None:
            raise RuntimeError("Claude response contained no text block")

        raw = text_block.text.strip()
        # Strip markdown fences if Claude wraps despite instructions
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude returned invalid JSON: {exc}\n---\n{raw[:500]}") from exc

        tasks_data = data.get("tasks")
        if not isinstance(tasks_data, list):
            raise RuntimeError(f"Expected 'tasks' list in Claude response; got: {type(tasks_data)}")

        sub_tasks: list[SubTask] = []
        for i, t in enumerate(tasks_data):
            try:
                sub_tasks.append(
                    SubTask(
                        id=t.get("id", f"task-{i+1}"),
                        title=t["title"],
                        description=t["description"],
                        files_touched=t.get("files_touched", []),
                        depends_on=t.get("depends_on", []),
                        priority=int(t.get("priority", 1)),
                        max_turns=int(t.get("max_turns", 30)),
                    )
                )
            except KeyError as exc:
                raise RuntimeError(
                    f"Sub-task at index {i} is missing required field {exc}; "
                    f"got keys: {list(t.keys())}"
                ) from exc
        return sub_tasks

    def _create_sub_issues(self, sub_tasks: list[SubTask]) -> list[int]:
        """Create a GitHub issue per sub-task and return their numbers.

        If creation fails for individual tasks, the error is logged and the
        loop continues so that successfully-created issues are not abandoned.
        This means the returned list may be shorter than ``sub_tasks`` when
        some tasks could not be created; the caller should check accordingly.
        """
        numbers: list[int] = []
        for task in sub_tasks:
            body = self._build_issue_body(task)
            try:
                result = self._gh([
                    "issue", "create",
                    "--title", task.title,
                    "--body", body,
                    "--label", self.READY_LABEL,
                ])
            except RuntimeError as exc:
                log.error(
                    "Failed to create sub-issue for task '%s': %s — skipping",
                    task.title,
                    exc,
                )
                continue
            # gh prints the URL of the created issue; extract the number
            url = result.stdout.strip()
            try:
                number = int(url.rstrip("/").split("/")[-1])
            except ValueError as exc:
                log.error(
                    "Could not parse issue number from gh output for task '%s': %r\n"
                    "Raw output: %r — skipping",
                    task.title,
                    exc,
                    url,
                )
                continue
            log.info("Created sub-issue #%d: %s", number, task.title)
            numbers.append(number)
        return numbers

    @staticmethod
    def _build_issue_body(task: SubTask) -> str:
        parts: list[str] = [task.description, ""]

        if task.files_touched:
            files_lines = "\n".join(f"- {f}" for f in task.files_touched)
            parts += [f"### Files\n{files_lines}", ""]

        if task.depends_on:
            parts += [f"### Depends on\n{', '.join(task.depends_on)}", ""]

        parts += [f"### Max turns\n{task.max_turns}", ""]
        return "\n".join(parts)

    def _ensure_decomposed_label(self) -> None:
        """Create the agent-decomposed label if it does not already exist."""
        try:
            check = self._gh(["label", "list", "--json", "name"])
        except RuntimeError as exc:
            log.warning(
                "Could not list labels (skipping label creation): %s", exc
            )
            return

        existing = {lbl["name"] for lbl in json.loads(check.stdout or "[]")}
        if self.DECOMPOSED_LABEL in existing:
            return

        try:
            self._gh([
                "label", "create",
                self.DECOMPOSED_LABEL,
                "--color", "8B5CF6",
                "--description", "Broken into sub-tasks",
            ])
        except RuntimeError as exc:
            log.warning(
                "Could not create label '%s': %s",
                self.DECOMPOSED_LABEL,
                exc,
            )

    def _finalize_parent_issue(self, sub_numbers: list[int]) -> None:
        """Post a summary comment and swap labels on the parent issue."""
        items = "\n".join(f"- #{n}" for n in sub_numbers)
        comment = (
            f"This issue has been decomposed into {len(sub_numbers)} sub-tasks:\n\n"
            f"{items}\n\n"
            f"Each sub-task has been created as a separate issue with the "
            f"`{self.READY_LABEL}` label."
        )
        self._gh(["issue", "comment", str(self.issue_number), "--body", comment])
        self._gh([
            "issue", "edit", str(self.issue_number),
            "--remove-label", self.READY_LABEL,
            "--add-label", self.DECOMPOSED_LABEL,
        ])

    def _gh(self, args: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
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


# ---------------------------------------------------------------------------
# Standalone vagueness check helper (used by orchestrator)
# ---------------------------------------------------------------------------

def is_vague(body: str | None) -> bool:
    """Return True if the issue body looks too vague for a worker to execute.

    An issue is considered vague when:
    - The body is shorter than 100 characters, OR
    - There is no '### Files' / 'Files:' section listing files to touch.
    """
    if not body or len(body.strip()) < 100:
        return True
    # Check for a Files section
    return not re.search(r"^\s*(?:#{1,3}\s+)?files\s*:?\s*$", body, re.IGNORECASE | re.MULTILINE)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Decompose a vague GitHub issue into agent-ready sub-tasks"
    )
    parser.add_argument(
        "--issue",
        type=int,
        required=True,
        help="GitHub issue number to decompose",
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

    agent = DecomposerAgent(issue_number=args.issue, repo_path=args.repo_path)
    try:
        result = agent.decompose()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Issue #{result.parent_issue_number} decomposed into {len(result.sub_issue_numbers)} sub-issues:")
    for n in result.sub_issue_numbers:
        print(f"  #{n}")


if __name__ == "__main__":
    main()
