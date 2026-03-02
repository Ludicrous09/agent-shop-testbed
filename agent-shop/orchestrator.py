"""Orchestrator: run agent workers in parallel according to a PLAN.yaml."""

import argparse
import asyncio
import functools
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_manager import load_tasks, get_ready_tasks
from worker import Task, Worker, WorkerResult
from reviewer import ReviewAgent, ReviewResult
from fixer import FixAgent
from issue_source import IssueSource
from decomposer import DecomposerAgent, is_vague
from conflict_resolver import ConflictResolver
from architect import ArchitectAgent
from claude_md_generator import run as generate_claude_md

from rich.console import Console
from rich.live import Live
from rich.table import Table

log = logging.getLogger("orchestrator")
console = Console()

PROMPTS_PER_TASK = 5

# Mergeability polling after conflict resolution
POST_RESOLVE_MERGEABILITY_MAX_RETRIES = 5
POST_RESOLVE_MERGEABILITY_RETRY_INTERVAL = 3  # seconds

# Type alias for the optional event callback
EventCallback = Callable[[str, dict], None] | None


def _fire_event(on_event: EventCallback, event_type: str, payload: dict) -> None:
    """Invoke the event callback if set, silently swallowing any exceptions."""
    if on_event is None:
        return
    try:
        on_event(event_type, payload)
    except Exception as exc:  # pragma: no cover
        log.warning("on_event callback raised for %r: %s", event_type, exc)


# ---------------------------------------------------------------------------
# Status tracking
# ---------------------------------------------------------------------------

class OrchestratorState:
    """Mutable state bag for the orchestration loop."""

    def __init__(self, tasks, repo_path: Path, timeout: int, log_dir: Path, max_retries: int = 2):
        self.tasks = tasks
        self.repo_path = repo_path
        self.timeout = timeout
        self.log_dir = log_dir
        self.max_retries = max_retries

        self.completed_ids: set[str] = set()
        self.failed_ids: set[str] = set()
        self.active_workers: dict[str, asyncio.Future] = {}  # task_id -> future
        self.review_futures: dict[str, asyncio.Future] = {}  # task_id -> review/fix/merge future
        self.active_files: set[str] = set()
        self.results: list[WorkerResult] = []
        self.retry_counts: dict[str, int] = {}  # task_id -> number of retries attempted
        self.task_start_times: dict[str, datetime] = {}  # task_id -> when worker was spawned
        self.task_durations: dict[str, float] = {}  # task_id -> elapsed seconds (completed/failed)
        self.task_costs: dict[str, float] = {}  # task_id -> cost_usd from claude output
        self.task_prompts: dict[str, int] = {}  # task_id -> num_turns from claude output
        self.current_priority: int | None = None  # priority group currently being processed

    # Convenience counts
    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def queued(self) -> int:
        done = (
            self.completed_ids
            | self.failed_ids
            | set(self.active_workers)
            | set(self.review_futures)
        )
        return self.total - len(done)

    @property
    def active(self) -> int:
        return len(self.active_workers)

    @property
    def reviewing(self) -> int:
        return len(self.review_futures)

    def status_dict(self) -> dict:
        now = datetime.now(timezone.utc)

        def _elapsed(tid: str) -> float | None:
            if tid in self.task_start_times:
                return (now - self.task_start_times[tid]).total_seconds()
            return None

        total_cost = sum(self.task_costs.values())
        total_prompts = sum(self.task_prompts.values())
        total_elapsed = sum(self.task_durations.values())
        for tid, start in self.task_start_times.items():
            if tid not in self.task_durations:
                total_elapsed += (now - start).total_seconds()

        return {
            "tasks": {
                "total": self.total,
                "queued": self.queued,
                "active": self.active,
                "reviewing": self.reviewing,
                "completed": len(self.completed_ids),
                "failed": len(self.failed_ids),
            },
            "workers": {
                **{
                    tid: {
                        "status": "running",
                        "retries": self.retry_counts.get(tid, 0),
                        "elapsed_seconds": _elapsed(tid),
                    }
                    for tid in self.active_workers
                },
                **{
                    tid: {
                        "status": "reviewing",
                        "retries": self.retry_counts.get(tid, 0),
                        "elapsed_seconds": _elapsed(tid),
                    }
                    for tid in self.review_futures
                },
            },
            "task_timing": {
                tid: {
                    "elapsed_seconds": self.task_durations.get(tid),
                    "cost_usd": self.task_costs.get(tid),
                    "num_turns": self.task_prompts.get(tid),
                }
                for tid in (self.completed_ids | self.failed_ids)
                if tid in self.task_durations or tid in self.task_costs
            },
            "summary": {
                "total_elapsed_seconds": total_elapsed,
                "total_cost_usd": total_cost,
                "total_prompts": total_prompts,
            },
            "current_priority": self.current_priority,
            "retries": dict(self.retry_counts),
            "prs": [
                {"task_id": r.task_id, "pr_url": r.pr_url, "pr_number": r.pr_number}
                for r in self.results
                if r.pr_url
            ],
        }


def write_status(state: OrchestratorState, status_path: Path) -> None:
    status_path.write_text(json.dumps(state.status_dict(), indent=2) + "\n")


# ---------------------------------------------------------------------------
# Rich display
# ---------------------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    """Format elapsed seconds as 'Xm YYs' or 'Xs'."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}m {secs:02d}s"


def build_table(state: OrchestratorState) -> Table:
    now = datetime.now(timezone.utc)

    table = Table(title="Orchestrator Status", expand=True)
    table.add_column("Task ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Priority", justify="center")
    table.add_column("Status", justify="center")
    table.add_column("Duration", justify="right")
    table.add_column("Cost", justify="right")

    total_cost = 0.0
    total_prompts = 0

    for task in state.tasks:
        if task.id in state.completed_ids:
            status = "[green]completed[/green]"
        elif task.id in state.failed_ids:
            status = "[red]failed[/red]"
        elif task.id in state.active_workers:
            status = "[yellow]running[/yellow]"
        elif task.id in state.review_futures:
            status = "[blue]reviewing[/blue]"
        else:
            status = "[dim]queued[/dim]"

        # Duration: live for running tasks, fixed for completed/failed
        if task.id in state.task_durations:
            duration_str = _format_duration(state.task_durations[task.id])
        elif task.id in state.task_start_times:
            elapsed = (now - state.task_start_times[task.id]).total_seconds()
            duration_str = f"[yellow]{_format_duration(elapsed)}[/yellow]"
        else:
            duration_str = ""

        # Cost
        cost = state.task_costs.get(task.id)
        if cost is not None:
            cost_str = f"${cost:.4f}"
            total_cost += cost
        else:
            cost_str = ""

        prompts = state.task_prompts.get(task.id)
        if prompts is not None:
            total_prompts += prompts

        table.add_row(task.id, task.title, str(task.priority), status, duration_str, cost_str)

    # Compute total elapsed including running tasks
    total_elapsed = sum(state.task_durations.values())
    for tid, start in state.task_start_times.items():
        if tid not in state.task_durations:
            total_elapsed += (now - start).total_seconds()

    cost_summary = f"${total_cost:.4f}" if total_cost > 0 else "N/A"
    prompts_summary = str(total_prompts) if total_prompts > 0 else "N/A"

    priority_str = (
        f"Priority Group: {state.current_priority}  |  "
        if state.current_priority is not None
        else ""
    )
    table.caption = (
        priority_str
        + f"Total: {state.total}  "
        f"Active: {state.active}  "
        f"Completed: {len(state.completed_ids)}  "
        f"Failed: {len(state.failed_ids)}  "
        f"| Elapsed: {_format_duration(total_elapsed)}  "
        f"Cost: {cost_summary}  "
        f"Prompts: {prompts_summary}"
    )
    return table


# ---------------------------------------------------------------------------
# Review follow-up issue helpers
# ---------------------------------------------------------------------------

_FOLLOWUP_LABEL_COLOR = "d8b4fe"  # light purple


def _clean_env() -> dict[str, str]:
    """Return a copy of os.environ without the CLAUDECODE variable."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def _summarize_review_comment(comment_text: str, filename: str, line: int) -> tuple[str, str]:
    """Use Claude CLI to generate a concise title and action summary.

    Returns (title_suffix, action_summary) where title_suffix is under 60 chars
    and action_summary is 1-2 sentences.  Falls back to truncated text on failure.
    """
    prompt = (
        "You are summarizing a code review comment for a GitHub issue title and body.\n\n"
        f"File: {filename}:{line}\n"
        f"Comment:\n{comment_text}\n\n"
        "Respond with EXACTLY two lines, nothing else:\n"
        "Line 1: A concise title suffix (under 55 chars) in the format: "
        f"{filename}: <brief description of the fix>\n"
        "Line 2: A 1-2 sentence summary of what action is needed.\n\n"
        "Do NOT include any markdown, prefixes, labels, or extra text."
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=60,
            env=_clean_env(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            lines = proc.stdout.strip().splitlines()
            title_suffix = lines[0].strip() if lines else ""
            action_summary = lines[1].strip() if len(lines) > 1 else ""
            if title_suffix and len(title_suffix) <= 80:
                return title_suffix, action_summary
    except (subprocess.TimeoutExpired, OSError):
        pass

    return _fallback_comment_title(comment_text, filename)


def _fallback_comment_title(comment_text: str, filename: str) -> tuple[str, str]:
    """Truncate the first sentence as a fallback title. Returns (title_suffix, '')."""
    brief = comment_text.split(".")[0].strip()
    if len(brief) > 57:
        brief = brief[:54] + "..."
    return f"{filename}: {brief}"[:60], ""


def _summarize_review_comments_batch(
    comments: list[tuple[str, str, int]],
) -> list[tuple[str, str]]:
    """Use a single Claude CLI call to generate titles and summaries for all comments.

    Takes a list of (comment_text, filename, line) tuples and returns a list of
    (title_suffix, action_summary) pairs in the same order.

    Falls back to the truncation strategy for all entries if the batch call fails
    or returns an unexpected response.
    """
    if not comments:
        return []

    comment_blocks = []
    for i, (text, filename, line) in enumerate(comments, 1):
        comment_blocks.append(f"[{i}] File: {filename}:{line}\n    Comment: {text}")

    prompt = (
        "You are summarizing code review comments for GitHub issue titles and bodies.\n\n"
        "For each comment below, generate a concise title suffix and action summary.\n\n"
        "Respond with ONLY a JSON array (one object per comment, in the same order):\n"
        '[{"title": "...", "summary": "..."}, ...]\n\n'
        "Rules:\n"
        '- title: under 55 chars, format: "{filename}: <brief description of the fix>"\n'
        "- summary: 1-2 sentences of what action is needed\n"
        "- No markdown, no extra text outside the JSON array\n\n"
        "Comments:\n\n" + "\n\n".join(comment_blocks)
    )

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "text"],
            capture_output=True,
            text=True,
            timeout=120,
            env=_clean_env(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            raw = proc.stdout.strip()
            # Strip markdown code fences if present
            if raw.startswith("```"):
                lines = raw.splitlines()
                end = -1 if lines[-1].strip() == "```" else len(lines)
                raw = "\n".join(lines[1:end])
            parsed = json.loads(raw)
            if isinstance(parsed, list) and len(parsed) == len(comments):
                results: list[tuple[str, str]] = []
                for item, (text, filename, _line) in zip(parsed, comments):
                    title = str(item.get("title", "")).strip()
                    summary = str(item.get("summary", "")).strip()
                    if title and len(title) <= 80:
                        results.append((title, summary))
                    else:
                        results.append(_fallback_comment_title(text, filename))
                return results
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, KeyError):
        pass

    # Fallback for all comments
    return [_fallback_comment_title(text, filename) for text, filename, _line in comments]


def _check_label_exists(repo_path: Path, label_name: str) -> bool:
    """Return True if *label_name* already exists in the repository."""
    proc = subprocess.run(
        ["gh", "label", "list", "--json", "name", "--limit", "200"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return False
    try:
        return label_name in {lbl["name"] for lbl in json.loads(proc.stdout)}
    except (json.JSONDecodeError, KeyError):
        return False


def _ensure_review_followup_label(repo_path: Path) -> None:
    """Create the 'review-followup' label if it doesn't already exist."""
    check = subprocess.run(
        ["gh", "label", "list", "--json", "name", "--limit", "200"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if check.returncode == 0:
        try:
            existing = {lbl["name"] for lbl in json.loads(check.stdout)}
            if "review-followup" in existing:
                return
        except (json.JSONDecodeError, KeyError):
            pass

    proc = subprocess.run(
        [
            "gh", "label", "create", "review-followup",
            "--color", _FOLLOWUP_LABEL_COLOR,
            "--description", "Follow-up issues from PR review warnings/suggestions",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        log.warning("Failed to create review-followup label: %s", proc.stderr[:300])
    else:
        log.info("Created 'review-followup' label")


def _get_pr_title(repo_path: Path, pr_number: int) -> str:
    """Fetch the PR title via gh. Returns empty string on failure."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "title", "--jq", ".title"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        pass
    return ""


def _create_followup_issues(
    repo_path: Path,
    review: ReviewResult,
    pr_number: int,
    pr_url: str,
) -> list[str]:
    """Create GitHub follow-up issues for WARNING and SUGGESTION review comments.

    Returns list of created issue URLs.
    Skips comments shorter than 20 chars and deduplicates against existing issues.
    The ``priority:3`` label is only added when it already exists in the repo to
    avoid silently dropping issues due to a missing label.
    """
    created_urls: list[str] = []
    priority_label_exists = _check_label_exists(repo_path, "priority:3")
    pr_title = _get_pr_title(repo_path, pr_number)

    # Collect all qualifying comments first so we can batch-summarize them
    qualifying: list = []
    for comment in review.comments:
        if comment.severity not in ("warning", "suggestion"):
            continue
        text = comment.comment.strip()
        if len(text) < 20:
            log.debug(
                "Skipping short review comment (%d chars) in %s:%d",
                len(text),
                comment.file,
                comment.line,
            )
            continue
        qualifying.append(comment)

    if not qualifying:
        return created_urls

    # Single Claude call for all comments instead of one subprocess per comment
    batch_input = [(c.comment.strip(), c.file, c.line) for c in qualifying]
    batch_results = _summarize_review_comments_batch(batch_input)

    for comment, (title_suffix, action_summary) in zip(qualifying, batch_results):
        text = comment.comment.strip()
        title = f"[Review Follow-up] {title_suffix}"
        if len(title) > 80:
            title = title[:77] + "..."

        # Deduplicate: search for open issues with the [Review Follow-up] prefix
        search_proc = subprocess.run(
            [
                "gh", "issue", "list",
                "--search", "[Review Follow-up] in:title",
                "--state", "open",
                "--json", "title",
                "--limit", "100",
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if search_proc.returncode == 0:
            try:
                existing_titles = {iss["title"] for iss in json.loads(search_proc.stdout)}
                if title in existing_titles:
                    log.info("Skipping duplicate follow-up issue: %s", title)
                    continue
            except (json.JSONDecodeError, KeyError):
                pass

        pr_ref = f"PR #{pr_number}"
        if pr_title:
            pr_ref += f" ({pr_title})"

        what_to_fix = action_summary if action_summary else text.split(".")[0].strip() + "."

        body = (
            "### Description\n"
            "\n"
            f"Review feedback from {pr_ref}:\n"
            "\n"
            f"**Severity:** {comment.severity.upper()}\n"
            f"**File:** `{comment.file}:{comment.line}`\n"
            "**Original comment:**\n"
            f"> {text}\n"
            "\n"
            f"**What to fix:** {what_to_fix}\n"
            "\n"
            "### Files\n"
            "\n"
            f"- {comment.file}\n"
            "\n"
            "### Max turns\n"
            "\n"
            "20\n"
        )

        create_cmd = [
            "gh", "issue", "create",
            "--title", title,
            "--body", body,
            "--label", "agent-ready",
            "--label", "review-followup",
        ]
        if priority_label_exists:
            create_cmd += ["--label", "priority:3"]

        create_proc = subprocess.run(
            create_cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if create_proc.returncode != 0:
            log.warning(
                "Failed to create follow-up issue for %s:%d: %s",
                comment.file,
                comment.line,
                create_proc.stderr[:300],
            )
        else:
            issue_url = create_proc.stdout.strip()
            log.info("Created follow-up issue: %s", issue_url)
            created_urls.append(issue_url)

    return created_urls


# ---------------------------------------------------------------------------
# Review / Fix / Merge pipeline
# ---------------------------------------------------------------------------

MAX_FIX_ATTEMPTS = 2

# When a fix agent is making measurable progress (error count decreasing each round),
# allow this many extra fix attempts beyond max_fix_attempts before giving up.
# The buffer rewards convergent behaviour without inflating the baseline limit for tasks
# that stall immediately.
EXTRA_FIX_ATTEMPTS_BUFFER = 3


def _review_fix_merge_sync(
    repo_path: Path,
    result: WorkerResult,
    max_fix_attempts: int = MAX_FIX_ATTEMPTS,
    extra_attempts_buffer: int = EXTRA_FIX_ATTEMPTS_BUFFER,
    on_event: EventCallback = None,
    task_title: str = "",
) -> tuple[bool, str]:
    """Blocking review/fix/merge cycle. Returns (True, '') on success or (False, error_msg) on failure."""
    pr = result.pr_number
    assert pr is not None
    pr_url = result.pr_url or f"PR #{pr}"

    # When the agent is making progress (error count decreasing each round), allow up to
    # extra_attempts_buffer fix attempts beyond max_fix_attempts before giving up.
    abs_max_fixes = max_fix_attempts + extra_attempts_buffer
    prev_error_count: int | None = None

    try:
        for attempt in range(abs_max_fixes + 1):
            log.info("Reviewing PR #%d (round %d)", pr, attempt + 1)
            _fire_event(on_event, "review_started", {"task_id": result.task_id, "pr_number": pr})
            try:
                reviewer = ReviewAgent(repo_path=repo_path, pr_number=pr)
                review = reviewer.review()
            except Exception as exc:
                log.error("ReviewAgent failed for PR #%d: %s\n%s", pr, exc, traceback.format_exc())
                error_msg = (
                    f"**Review step failed** for {pr_url}\n\n"
                    f"**Error type:** `{type(exc).__name__}`\n\n"
                    f"See server logs for full details."
                )
                return False, error_msg

            # Count and log findings per round for observability and progress tracking
            error_count = sum(1 for c in review.comments if c.severity == "error")
            warning_count = sum(1 for c in review.comments if c.severity == "warning")
            suggestion_count = sum(1 for c in review.comments if c.severity == "suggestion")
            log.info(
                "PR #%d round %d findings: %d error(s), %d warning(s), %d suggestion(s)",
                pr, attempt + 1, error_count, warning_count, suggestion_count,
            )

            if review.verdict == "approve":
                log.info("PR #%d approved — checking mergeability before merge", pr)
                _fire_event(on_event, "review_approved", {"task_id": result.task_id, "pr_number": pr, "pr_url": pr_url})
                # Sleep briefly to allow GitHub to compute merge status after the push
                time.sleep(2)

                mergeable_proc = subprocess.run(
                    ["gh", "pr", "view", str(pr), "--json", "mergeable", "--jq", ".mergeable"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if mergeable_proc.returncode != 0:
                    log.error(
                        "PR #%d mergeability check failed (gh command error): %s",
                        pr,
                        mergeable_proc.stderr.strip(),
                    )
                    error_msg = (
                        f"**Merge step failed** for {pr_url}\n\n"
                        f"gh command failed: {mergeable_proc.stderr.strip()}"
                    )
                    return False, error_msg
                mergeable = mergeable_proc.stdout.strip()
                log.info("PR #%d mergeability: %s", pr, mergeable)

                for _retry in range(3):
                    if mergeable != "UNKNOWN":
                        break
                    log.info("PR #%d mergeability UNKNOWN, sleeping 5s then retrying (attempt %d/3)", pr, _retry + 1)
                    time.sleep(5)
                    retry_unknown_proc = subprocess.run(
                        ["gh", "pr", "view", str(pr), "--json", "mergeable", "--jq", ".mergeable"],
                        cwd=repo_path,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if retry_unknown_proc.returncode != 0:
                        log.warning(
                            "PR #%d mergeability retry failed (gh error): %s",
                            pr,
                            retry_unknown_proc.stderr.strip(),
                        )
                        break
                    retry_mergeable = retry_unknown_proc.stdout.strip()
                    if not retry_mergeable:
                        log.warning(
                            "PR #%d mergeability retry %d/3 returned empty response, skipping",
                            pr,
                            _retry + 1,
                        )
                        break
                    mergeable = retry_mergeable
                    log.info("PR #%d mergeability: %s", pr, mergeable)

                if mergeable == "UNKNOWN":
                    mergeable = "CONFLICTING"

                if mergeable == "CONFLICTING":
                    log.info(
                        "PR #%d is %s — attempting automatic conflict resolution",
                        pr,
                        mergeable,
                    )
                    resolver = ConflictResolver(repo_path=repo_path, pr_number=pr)
                    conflict_result = resolver.resolve()
                    if conflict_result.success:
                        log.info(
                            "Conflicts resolved in PR #%d (%d files) — re-checking mergeability",
                            pr,
                            len(conflict_result.resolved_files),
                        )
                        # Sleep to allow GitHub to recompute merge status after push
                        time.sleep(2)
                        mergeable = "UNKNOWN"
                        for _post_attempt in range(POST_RESOLVE_MERGEABILITY_MAX_RETRIES):
                            retry_mergeable_proc = subprocess.run(
                                ["gh", "pr", "view", str(pr), "--json", "mergeable", "--jq", ".mergeable"],
                                cwd=repo_path,
                                capture_output=True,
                                text=True,
                                timeout=60,
                            )
                            if retry_mergeable_proc.returncode != 0:
                                log.error(
                                    "PR #%d post-resolution mergeability check failed: %s",
                                    pr,
                                    retry_mergeable_proc.stderr.strip(),
                                )
                                error_msg = (
                                    f"**Merge step failed** for {pr_url}\n\n"
                                    f"gh command failed after conflict resolution: "
                                    f"{retry_mergeable_proc.stderr.strip()}"
                                )
                                return False, error_msg
                            mergeable = retry_mergeable_proc.stdout.strip()
                            log.info("PR #%d mergeability after resolution: %s", pr, mergeable)
                            if mergeable != "UNKNOWN":
                                break
                            if _post_attempt < POST_RESOLVE_MERGEABILITY_MAX_RETRIES - 1:
                                log.info(
                                    "PR #%d mergeability UNKNOWN after conflict resolution, "
                                    "retrying (attempt %d/%d)...",
                                    pr,
                                    _post_attempt + 1,
                                    POST_RESOLVE_MERGEABILITY_MAX_RETRIES,
                                )
                                time.sleep(POST_RESOLVE_MERGEABILITY_RETRY_INTERVAL)
                        if mergeable != "MERGEABLE":
                            log.error(
                                "PR #%d still not mergeable after conflict resolution (status: %s)",
                                pr,
                                mergeable,
                            )
                            error_msg = (
                                f"**Merge step failed** for {pr_url}\n\n"
                                f"PR is not mergeable after conflict resolution "
                                f"(status: `{mergeable}`)."
                            )
                            return False, error_msg
                    else:
                        log.error(
                            "Conflict resolution failed for PR #%d: %s",
                            pr,
                            conflict_result.error,
                        )
                        error_msg = (
                            f"**Merge step failed** for {pr_url}\n\n"
                            f"Conflict resolution failed: {conflict_result.error}"
                        )
                        return False, error_msg
                elif mergeable != "MERGEABLE":
                    log.error(
                        "PR #%d is not mergeable (status: %s) — skipping merge",
                        pr,
                        mergeable,
                    )
                    error_msg = (
                        f"**Merge step failed** for {pr_url}\n\n"
                        f"PR is not mergeable (status: `{mergeable}`)."
                    )
                    return False, error_msg

                log.info("PR #%d is MERGEABLE — merging", pr)
                proc = subprocess.run(
                    ["gh", "pr", "merge", str(pr), "--squash", "--delete-branch"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if proc.returncode != 0:
                    log.error(
                        "gh pr merge failed for PR #%d (code %d): %s",
                        pr,
                        proc.returncode,
                        proc.stderr[:500],
                    )
                    error_msg = (
                        f"**Merge step failed** for {pr_url}\n\n"
                        f"`gh pr merge` exited with code {proc.returncode}.\n\n"
                        f"**stderr:**\n```\n{proc.stderr[:500]}\n```"
                    )
                    return False, error_msg

                log.info("PR #%d merged — pulling latest main", pr)
                _fire_event(on_event, "merge_completed", {"task_id": result.task_id, "pr_number": pr, "pr_url": pr_url})
                pull = subprocess.run(
                    ["git", "pull"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if pull.returncode != 0:
                    log.warning("git pull after merge failed: %s", pull.stderr[:300])

                # Create follow-up issues for WARNING/SUGGESTION review comments
                try:
                    _ensure_review_followup_label(repo_path)
                    followup_urls = _create_followup_issues(repo_path, review, pr, pr_url)
                    if followup_urls:
                        items = "\n".join(f"- {u}" for u in followup_urls)
                        summary_body = (
                            "## Review Follow-up Issues Created\n\n"
                            "The following issues were created from review "
                            "warnings/suggestions:\n\n"
                            f"{items}"
                        )
                        comment_proc = subprocess.run(
                            ["gh", "pr", "comment", str(pr), "--body", summary_body],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                            timeout=120,
                        )
                        if comment_proc.returncode != 0:
                            log.warning(
                                "Failed to post follow-up summary comment on PR #%d: %s",
                                pr,
                                comment_proc.stderr[:300],
                            )
                        else:
                            log.info(
                                "Posted follow-up summary on PR #%d (%d issues)",
                                pr,
                                len(followup_urls),
                            )
                except Exception as exc:
                    log.warning(
                        "Error creating follow-up issues for PR #%d: %s", pr, exc
                    )

                return True, ""

            # verdict == "request_changes"
            making_progress = prev_error_count is not None and error_count < prev_error_count
            if attempt >= max_fix_attempts:
                if not making_progress or attempt >= abs_max_fixes:
                    log.error(
                        "PR #%d still not approved after %d fix attempt(s) — marking failed",
                        pr,
                        attempt,
                    )
                    error_msg = (
                        f"**Review step failed** for {pr_url}\n\n"
                        f"PR was not approved after {attempt} fix attempt(s). "
                        f"Last review verdict: `{review.verdict}`."
                    )
                    return False, error_msg
                log.info(
                    "PR #%d: exceeded base fix limit (%d/%d) but making progress "
                    "(%d → %d error(s)) — allowing extra fix attempt",
                    pr,
                    attempt,
                    max_fix_attempts,
                    prev_error_count,
                    error_count,
                )
            prev_error_count = error_count
            _fire_event(on_event, "review_fix_needed", {"task_id": result.task_id, "pr_number": pr, "attempt": attempt + 1})

            log.info(
                "PR #%d needs changes — running FixAgent (fix %d)",
                pr,
                attempt + 1,
            )
            try:
                fixer = FixAgent(repo_path=repo_path, pr_number=pr)
                fix_result = fixer.fix()
            except Exception as exc:
                log.error("FixAgent raised for PR #%d: %s\n%s", pr, exc, traceback.format_exc())
                error_msg = (
                    f"**Fix step failed** for {pr_url}\n\n"
                    f"**Error type:** `{type(exc).__name__}`\n\n"
                    f"See server logs for full details."
                )
                return False, error_msg

            if not fix_result.success:
                log.error("FixAgent failed for PR #%d: %s", pr, fix_result.error)
                error_msg = (
                    f"**Fix step failed** for {pr_url}\n\n"
                    f"FixAgent reported failure: {fix_result.error}"
                )
                return False, error_msg

            log.info("Fix applied to PR #%d — re-reviewing", pr)

        return False, f"**Review/fix/merge cycle exhausted** for {pr_url}"  # unreachable but satisfies type checkers

    except subprocess.TimeoutExpired as exc:
        cmd_str = " ".join(exc.cmd) if isinstance(exc.cmd, list) else str(exc.cmd)
        log.error(
            "subprocess timed out in review/fix/merge for PR #%d: `%s` (timeout=%s s)",
            pr,
            cmd_str,
            exc.timeout,
        )
        error_msg = (
            f"**Timeout** in review/fix/merge cycle for {pr_url}\n\n"
            f"Command `{cmd_str}` timed out after {exc.timeout}s."
        )
        return False, error_msg
    except Exception as exc:
        tb = traceback.format_exc()
        log.error("Unexpected error in review/fix/merge for PR #%d: %s\n%s", pr, exc, tb)
        error_msg = (
            f"**Review step failed** for {pr_url}\n\n"
            f"**Error type:** `{type(exc).__name__}`\n\n"
            f"See server logs for details."
        )
        return False, error_msg


async def run_review_fix_merge(
    executor: ThreadPoolExecutor,
    repo_path: Path,
    result: WorkerResult,
    max_fix_attempts: int = MAX_FIX_ATTEMPTS,
    on_event: EventCallback = None,
    task_title: str = "",
) -> tuple[bool, str]:
    """Run _review_fix_merge_sync in a thread via run_in_executor."""
    loop = asyncio.get_running_loop()
    fn = functools.partial(
        _review_fix_merge_sync,
        max_fix_attempts=max_fix_attempts,
        on_event=on_event,
        task_title=task_title,
    )
    return await loop.run_in_executor(executor, fn, repo_path, result)


# ---------------------------------------------------------------------------
# Dry-run plan printer
# ---------------------------------------------------------------------------


def dry_run_plan(tasks: list[Task], max_workers: int) -> None:
    """Print the execution plan without spawning any workers."""
    console.rule("[bold cyan]Dry Run — Execution Plan")
    console.print(f"\n[bold]Tasks loaded:[/bold] {len(tasks)}")

    # Simulate execution waves to show parallelism and dependency ordering
    completed: set[str] = set()
    remaining = list(tasks)
    waves: list[list[Task]] = []

    while remaining:
        # Find tasks whose dependencies are all satisfied
        deps_ready = [
            t for t in remaining if all(dep in completed for dep in t.depends_on)
        ]
        if not deps_ready:
            # Circular or unresolvable dependency — break to avoid infinite loop
            unscheduled = [t.id for t in remaining]
            console.print(
                f"[bold red]Warning: {len(unscheduled)} task(s) could not be scheduled "
                f"(circular or missing dependency): {', '.join(unscheduled)}[/bold red]"
            )
            break

        # Within this wave, pick tasks that don't conflict on files
        wave_tasks: list[Task] = []
        wave_files: set[str] = set()
        for task in sorted(deps_ready, key=lambda t: t.priority):
            if not (wave_files & set(task.files_touched)):
                wave_tasks.append(task)
                wave_files.update(task.files_touched)

        waves.append(wave_tasks)
        for t in wave_tasks:
            completed.add(t.id)
            remaining.remove(t)

    # Print waves
    console.print()
    for i, wave_tasks in enumerate(waves):
        parallel = min(len(wave_tasks), max_workers)
        console.print(
            f"[bold]Wave {i + 1}[/bold] "
            f"({len(wave_tasks)} task(s), up to {parallel} running in parallel):"
        )
        for task in wave_tasks:
            deps_str = (
                f" [dim][depends on: {', '.join(task.depends_on)}][/dim]"
                if task.depends_on
                else ""
            )
            files_str = (
                f" [dim][files: {', '.join(task.files_touched)}][/dim]"
                if task.files_touched
                else ""
            )
            console.print(f"  • [cyan]{task.id}[/cyan]: {task.title}{deps_str}{files_str}")

    # File conflict groups — tasks sharing files that must run sequentially
    file_to_tasks: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        for f in task.files_touched:
            file_to_tasks[f].append(task.id)

    conflict_files = {f: tids for f, tids in file_to_tasks.items() if len(tids) > 1}

    console.print()
    if conflict_files:
        console.print("[bold yellow]File Conflict Groups (must run sequentially):[/bold yellow]")
        for f, tids in conflict_files.items():
            console.print(f"  [yellow]{f}[/yellow]: {', '.join(tids)}")
    else:
        console.print("[dim]No file conflicts detected.[/dim]")

    # Estimated prompt usage
    estimated_prompts = len(tasks) * PROMPTS_PER_TASK
    console.print()
    console.print(
        f"[bold]Estimated prompt usage:[/bold] "
        f"{len(tasks)} tasks × ~{PROMPTS_PER_TASK} prompts/task = ~{estimated_prompts} prompts"
    )
    console.print(f"[bold]Execution waves:[/bold] {len(waves)}")
    console.rule("[bold cyan]End of Dry Run")


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------

def _cleanup_failed_branch(repo_path: Path, branch: str) -> None:
    """Delete a failed branch from remote and locally."""
    remote_result = subprocess.run(
        ["git", "push", "origin", "--delete", branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if remote_result.returncode != 0:
        log.warning(
            "Failed to delete remote branch %s: %s",
            branch,
            remote_result.stderr[:300],
        )

    local_result = subprocess.run(
        ["git", "branch", "-D", branch],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if local_result.returncode != 0:
        log.warning(
            "Failed to delete local branch %s: %s",
            branch,
            local_result.stderr[:300],
        )


def _enrich_task_with_architect(task: Task, repo_path: Path) -> None:
    """Run ArchitectAgent on *task* and prepend the spec to task.description.

    Extracts the issue number from a task ID of the form ``issue-N``.  If the
    task ID does not follow that pattern, or if the architect call raises, a
    warning is logged and the task description is left unchanged so that the
    worker can still proceed.
    """
    if not task.id.startswith("issue-"):
        log.info(
            "Task %s is not from a GitHub issue — skipping architect enrichment",
            task.id,
        )
        return

    try:
        issue_number = int(task.id.split("-", 1)[1])
    except (IndexError, ValueError):
        log.warning("Could not parse issue number from task id %r", task.id)
        return

    try:
        agent = ArchitectAgent(issue_number=issue_number, repo_path=repo_path)
        spec = agent.design()
        task.description = (
            f"## Architect Spec\n{spec}\n\n## Original Issue\n{task.description}"
        )
        log.info(
            "Enriched task %s with architect spec (%d chars)", task.id, len(spec)
        )
    except Exception as exc:
        log.warning(
            "ArchitectAgent failed for task %s: %s — continuing without spec",
            task.id,
            exc,
        )


async def run_worker(
    executor: ThreadPoolExecutor,
    state: OrchestratorState,
    task: Task,
    worker_counter: int,
    branch_suffix: str = "",
    use_architect: bool = False,
) -> WorkerResult:
    """Run a single Worker.run() in a thread via run_in_executor.

    When *use_architect* is True, runs :func:`_enrich_task_with_architect` in
    the executor first so that the worker receives an augmented description.
    """
    loop = asyncio.get_running_loop()
    if use_architect:
        await loop.run_in_executor(
            executor, _enrich_task_with_architect, task, state.repo_path
        )
    worker = Worker(
        repo_path=state.repo_path,
        task=task,
        worker_id=f"worker-{worker_counter}",
        timeout=state.timeout,
        branch_suffix=branch_suffix,
        log_dir=state.log_dir,
    )
    return await loop.run_in_executor(executor, worker.run)


def _run_decomposer_pass(repo: Path, label: str) -> None:
    """Fetch agent-ready issues, decompose any that are vague, then return.

    Vague issues are those whose body is shorter than 100 characters or that
    have no ``Files:`` section.  The decomposer creates sub-issues and
    relabels the original as ``agent-decomposed``.
    """
    result = subprocess.run(
        [
            "gh", "issue", "list",
            "--label", label,
            "--limit", "9999",
            "--state", "open",
            "--json", "number,title,body",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("Failed to list issues for decomposition: %s", result.stderr[:300])
        return

    issues = json.loads(result.stdout)
    vague_issues = [iss for iss in issues if is_vague(iss.get("body") or "")]

    if not vague_issues:
        log.info("No vague issues found — skipping decomposition pass")
        return

    log.info(
        "Found %d vague issue(s) to decompose: %s",
        len(vague_issues),
        [iss["number"] for iss in vague_issues],
    )

    for iss in vague_issues:
        log.info("Decomposing issue #%d: %s", iss["number"], iss["title"])
        try:
            agent = DecomposerAgent(issue_number=iss["number"], repo_path=repo)
            decomposed = agent.decompose()
            log.info(
                "Issue #%d decomposed into %d sub-issues: %s",
                decomposed.parent_issue_number,
                len(decomposed.sub_issue_numbers),
                decomposed.sub_issue_numbers,
            )
        except Exception as exc:
            log.error("Failed to decompose issue #%d: %s", iss["number"], exc)


def _send_desktop_notification(completed: int, failed: int, elapsed: float) -> None:
    """Send a desktop notification summarising the run. Silently ignored on failure."""
    message = f"{completed} completed, {failed} failed ({_format_duration(elapsed)})"
    try:
        subprocess.run(
            ["notify-send", "Agent Shop", message],
            check=False,
            timeout=5,
        )
    except Exception:  # noqa: BLE001
        pass


async def orchestrate(
    plan_path: str,
    max_workers: int,
    repo_path: str,
    timeout: int,
    source: str = "plan",
    label: str = "agent-ready",
    issue_number: int | None = None,
    max_retries: int = 2,
    max_fix_attempts: int = MAX_FIX_ATTEMPTS,
    log_dir: str = "./logs",
    dry_run: bool = False,
    decompose: bool = False,
    architect: bool = False,
    max_priority: int | None = None,
    generate_claude_md_flag: bool = False,
    notify: bool = True,
    on_event: EventCallback = None,
) -> None:
    repo = Path(repo_path).resolve()
    # status.json stays local to the orchestrator's working directory,
    # not inside the (potentially external) target repo.
    status_path = Path("status.json")
    log_dir_path = Path(log_dir)

    if generate_claude_md_flag and not dry_run:
        log.info("--generate-claude-md set: generating CLAUDE.md for %s", repo)
        written = generate_claude_md(repo)
        if written:
            log.info("CLAUDE.md generated at %s", repo / "CLAUDE.md")
        else:
            log.info("CLAUDE.md already exists at %s — skipping generation", repo / "CLAUDE.md")

    if decompose and source != "issues":
        log.warning(
            "--decompose has no effect when --source is not 'issues' (got %r); ignoring",
            source,
        )
    if decompose and source == "issues":
        log.info("Running decomposer pass before fetching tasks (label=%s, repo=%s)", label, repo)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_decomposer_pass, repo, label)

    if source == "issues":
        if issue_number is not None:
            log.info("Fetching single issue #%d from GitHub (repo=%s)", issue_number, repo)
        else:
            log.info("Fetching tasks from GitHub Issues (label=%s, repo=%s)", label, repo)
        issue_source = IssueSource(repo_path=repo, label=label, issue_number=issue_number)
        tasks = issue_source.fetch_tasks()
    else:
        log.info("Loading plan from %s", plan_path)
        tasks = load_tasks(plan_path)
        issue_source = None
    log.info("Loaded %d tasks", len(tasks))

    if dry_run:
        dry_run_plan(tasks, max_workers)
        return

    state = OrchestratorState(tasks, repo, timeout, log_dir_path, max_retries=max_retries)
    worker_counter = 0
    executor = ThreadPoolExecutor(max_workers=max_workers)

    # Group tasks by priority level and process one group at a time
    priority_groups: dict[int, list[Task]] = defaultdict(list)
    for task in tasks:
        priority_groups[task.priority].append(task)
    sorted_priorities = sorted(priority_groups.keys())
    current_priority_idx = 0

    if sorted_priorities:
        state.current_priority = sorted_priorities[0]
        log.info(
            "Starting with priority group %d (%d tasks)",
            sorted_priorities[0],
            len(priority_groups[sorted_priorities[0]]),
        )

    loop_start = time.monotonic()
    _fire_event(on_event, "run_started", {"tasks": [t.id for t in tasks], "total": len(tasks)})

    try:
        with Live(build_table(state), console=console, refresh_per_second=2) as live:
            while True:
                # Check for completed worker futures
                done_ids = [tid for tid, f in state.active_workers.items() if f.done()]

                for task_id in done_ids:
                    future = state.active_workers.pop(task_id)
                    task_obj = next(t for t in tasks if t.id == task_id)

                    # Remove files from active set
                    for f in task_obj.files_touched:
                        state.active_files.discard(f)

                    try:
                        result: WorkerResult = future.result()
                        state.results.append(result)
                        # Record elapsed time and cost from the finished worker
                        state.task_durations[task_id] = result.elapsed_seconds
                        if result.cost_usd is not None:
                            state.task_costs[task_id] = result.cost_usd
                        if result.num_turns is not None:
                            state.task_prompts[task_id] = result.num_turns
                        if result.success and result.pr_number is not None:
                            # Launch review/fix/merge pipeline
                            log.info(
                                "Task %s created PR #%d — launching review pipeline",
                                task_id,
                                result.pr_number,
                            )
                            rfm_future = asyncio.ensure_future(
                                run_review_fix_merge(
                                    executor,
                                    repo,
                                    result,
                                    max_fix_attempts=max_fix_attempts,
                                    on_event=on_event,
                                    task_title=task_obj.title,
                                )
                            )
                            state.review_futures[task_id] = rfm_future
                        elif result.success:
                            state.completed_ids.add(task_id)
                            log.info("Task %s completed (no PR)", task_id)
                            _fire_event(on_event, "task_completed", {
                                "task_id": task_id,
                                "title": task_obj.title,
                                "pr_url": None,
                                "pr_number": None,
                                "elapsed": result.elapsed_seconds,
                                "cost": result.cost_usd,
                            })
                        else:
                            retry_count = state.retry_counts.get(task_id, 0)
                            if retry_count < state.max_retries:
                                retry_n = retry_count + 1
                                state.retry_counts[task_id] = retry_n
                                log.warning(
                                    "Task %s failed (attempt %d/%d): %s — retrying",
                                    task_id,
                                    retry_n,
                                    state.max_retries + 1,
                                    result.error,
                                )
                                _cleanup_failed_branch(repo, result.branch)
                                branch_suffix = f"-retry-{retry_n}"
                                worker_counter += 1
                                log.info(
                                    "Spawning retry worker for task %s (suffix=%s)",
                                    task_id,
                                    branch_suffix,
                                )
                                state.active_files.update(task_obj.files_touched)
                                retry_future = asyncio.ensure_future(
                                    run_worker(
                                        executor, state, task_obj, worker_counter,
                                        branch_suffix, use_architect=architect,
                                    )
                                )
                                state.active_workers[task_id] = retry_future
                                state.task_durations.pop(task_id, None)
                                state.task_start_times[task_id] = datetime.now(timezone.utc)
                            else:
                                state.failed_ids.add(task_id)
                                log.error(
                                    "Task %s failed after %d retries: %s",
                                    task_id,
                                    state.max_retries,
                                    result.error,
                                )
                                _fire_event(on_event, "task_failed", {
                                    "task_id": task_id,
                                    "title": task_obj.title,
                                    "error": result.error,
                                })
                    except Exception as exc:
                        retry_count = state.retry_counts.get(task_id, 0)
                        if retry_count < state.max_retries:
                            retry_n = retry_count + 1
                            state.retry_counts[task_id] = retry_n
                            log.warning(
                                "Task %s raised exception (attempt %d/%d): %s — retrying",
                                task_id,
                                retry_n,
                                state.max_retries + 1,
                                exc,
                            )
                            branch_suffix = f"-retry-{retry_n}"
                            worker_counter += 1
                            state.active_files.update(task_obj.files_touched)
                            retry_future = asyncio.ensure_future(
                                run_worker(
                                    executor, state, task_obj, worker_counter,
                                    branch_suffix, use_architect=architect,
                                )
                            )
                            state.active_workers[task_id] = retry_future
                            state.task_durations.pop(task_id, None)
                            state.task_start_times[task_id] = datetime.now(timezone.utc)
                        else:
                            state.failed_ids.add(task_id)
                            log.error("Task %s raised exception: %s", task_id, exc)
                            _fire_event(on_event, "task_failed", {
                                "task_id": task_id,
                                "title": task_obj.title,
                                "error": str(exc),
                            })

                    write_status(state, status_path)
                    live.update(build_table(state))

                # Check for completed review/fix/merge futures
                done_review_ids = [tid for tid, f in state.review_futures.items() if f.done()]

                for task_id in done_review_ids:
                    future = state.review_futures.pop(task_id)
                    try:
                        merged, error_msg = future.result()
                        task_result = next((r for r in state.results if r.task_id == task_id), None)
                        task_title_str = next((t.title for t in tasks if t.id == task_id), "")
                        if merged:
                            state.completed_ids.add(task_id)
                            log.info("Task %s merged successfully", task_id)
                            _fire_event(on_event, "task_completed", {
                                "task_id": task_id,
                                "title": task_title_str,
                                "pr_url": task_result.pr_url if task_result else None,
                                "pr_number": task_result.pr_number if task_result else None,
                                "elapsed": state.task_durations.get(task_id),
                                "cost": state.task_costs.get(task_id),
                            })
                            # Close GitHub issue if using issues source
                            if issue_source and task_id.startswith("issue-"):
                                pr_url = task_result.pr_url if task_result else None
                                try:
                                    issue_source.mark_complete(task_id, pr_url or "N/A")
                                except Exception as exc:
                                    log.warning("Failed to close issue for %s: %s", task_id, exc)
                        else:
                            state.failed_ids.add(task_id)
                            log.error("Task %s failed review/fix/merge cycle", task_id)
                            _fire_event(on_event, "task_failed", {
                                "task_id": task_id,
                                "title": task_title_str,
                                "error": error_msg or "Review/fix/merge cycle failed",
                            })
                            if issue_source and task_id.startswith("issue-"):
                                try:
                                    issue_source.mark_failed(task_id, error_msg or "Review/fix/merge cycle failed")
                                except Exception as exc:
                                    log.warning("Failed to mark issue failed for %s: %s", task_id, exc)
                    except Exception as exc:
                        tb = traceback.format_exc()
                        state.failed_ids.add(task_id)
                        log.error("Task %s review pipeline raised: %s\n%s", task_id, exc, tb)
                        task_title_str = next((t.title for t in tasks if t.id == task_id), "")
                        _fire_event(on_event, "task_failed", {
                            "task_id": task_id,
                            "title": task_title_str,
                            "error": str(exc),
                        })
                        if issue_source and task_id.startswith("issue-"):
                            pr_url = next((r.pr_url for r in state.results if r.task_id == task_id), None)
                            pr_ref = pr_url or f"task {task_id}"
                            error_msg = (
                                f"**Review step failed** for {pr_ref}\n\n"
                                f"**Error type:** `{type(exc).__name__}`\n\n"
                                f"See server logs for details."
                            )
                            try:
                                issue_source.mark_failed(task_id, error_msg)
                            except Exception as mark_exc:
                                log.warning("Failed to mark issue failed for %s: %s", task_id, mark_exc)

                    write_status(state, status_path)
                    live.update(build_table(state))

                # Check if current priority group is complete; advance to next if so
                if sorted_priorities and current_priority_idx < len(sorted_priorities):
                    current_priority = sorted_priorities[current_priority_idx]
                    current_group_ids = {t.id for t in priority_groups[current_priority]}
                    group_settled = current_group_ids & (state.completed_ids | state.failed_ids)
                    group_in_flight = current_group_ids & (
                        set(state.active_workers) | set(state.review_futures)
                    )

                    if len(group_settled) == len(current_group_ids) and not group_in_flight:
                        group_completed = len(current_group_ids & state.completed_ids)
                        group_failed = len(current_group_ids & state.failed_ids)
                        log.info(
                            "Priority %d complete: %d succeeded, %d failed",
                            current_priority,
                            group_completed,
                            group_failed,
                        )
                        if max_priority is not None and current_priority >= max_priority:
                            log.info(
                                "Stopping after priority %d (--max-priority)",
                                current_priority,
                            )
                            break
                        if current_priority_idx + 1 < len(sorted_priorities):
                            current_priority_idx += 1
                            state.current_priority = sorted_priorities[current_priority_idx]
                            log.info(
                                "Advancing to priority group %d (%d tasks)",
                                state.current_priority,
                                len(priority_groups[state.current_priority]),
                            )
                            write_status(state, status_path)
                            live.update(build_table(state))

                # Are we done?
                all_settled = state.completed_ids | state.failed_ids
                if len(all_settled) == state.total:
                    log.info("All tasks settled — exiting")
                    break

                # Determine ready tasks for the current priority group
                if sorted_priorities and current_priority_idx < len(sorted_priorities):
                    current_priority = sorted_priorities[current_priority_idx]
                    current_group_ids = {t.id for t in priority_groups[current_priority]}
                else:
                    current_group_ids = set()

                ready = get_ready_tasks(
                    tasks,
                    state.completed_ids,
                    state.active_files,
                )
                # Filter to current priority group and exclude already-active/settled tasks
                ready = [
                    t for t in ready
                    if t.id in current_group_ids
                    and t.id not in state.active_workers
                    and t.id not in state.failed_ids
                    and t.id not in state.review_futures
                    and t.id not in state.completed_ids
                ]

                # Safety exit: nothing running, nothing to spawn — avoid infinite loop
                if state.active == 0 and state.reviewing == 0 and not ready:
                    log.info("No active workers, no reviews pending, no tasks ready — exiting")
                    break

                slots = max_workers - state.active
                for task in ready[:slots]:
                    worker_counter += 1
                    log.info("Spawning worker for task %s", task.id)
                    state.active_files.update(task.files_touched)
                    future = asyncio.ensure_future(
                        run_worker(
                            executor, state, task, worker_counter,
                            use_architect=architect,
                        )
                    )
                    state.active_workers[task.id] = future
                    state.task_start_times[task.id] = datetime.now(timezone.utc)
                    _fire_event(on_event, "task_started", {"task_id": task.id, "title": task.title, "worker_id": worker_counter})
                    write_status(state, status_path)
                    live.update(build_table(state))

                # Brief sleep to avoid busy-looping
                await asyncio.sleep(2)

    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    # Final summary
    console.print()
    console.rule("[bold green]Orchestration Complete")
    console.print(f"  Completed: {len(state.completed_ids)}")
    console.print(f"  Failed:    {len(state.failed_ids)}")
    total_elapsed = sum(state.task_durations.values())
    total_cost = sum(state.task_costs.values())
    total_prompts = sum(state.task_prompts.values())
    console.print(f"  Elapsed:   {_format_duration(total_elapsed)}")
    if total_cost > 0:
        console.print(f"  Cost:      ${total_cost:.4f}")
    if total_prompts > 0:
        console.print(f"  Prompts:   {total_prompts}")
    for r in state.results:
        if r.pr_url:
            console.print(f"  PR: {r.pr_url}")
    write_status(state, status_path)
    _fire_event(on_event, "run_completed", {
        "completed": len(state.completed_ids),
        "failed": len(state.failed_ids),
        "total_cost": total_cost,
        "total_elapsed": total_elapsed,
        "prs": [
            {"task_id": r.task_id, "pr_url": r.pr_url, "pr_number": r.pr_number}
            for r in state.results
            if r.pr_url
        ],
    })

    if notify:
        wall_clock_elapsed = time.monotonic() - loop_start
        _send_desktop_notification(
            len(state.completed_ids),
            len(state.failed_ids),
            wall_clock_elapsed,
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-shop orchestrator")
    parser.add_argument(
        "--plan", default="PLAN.yaml", help="Path to PLAN.yaml (default: PLAN.yaml)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Max parallel workers (default: 2)",
    )
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Path to the git repo (default: cwd)",
    )
    parser.add_argument(
        "--source",
        choices=["plan", "issues"],
        default="plan",
        help="Task source: 'plan' for PLAN.yaml, 'issues' for GitHub Issues (default: plan)",
    )
    parser.add_argument(
        "--label",
        default="agent-ready",
        help="GitHub Issues label to use as task source (default: agent-ready)",
    )
    parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help=(
            "Target a single GitHub issue number instead of all issues with --label. "
            "Only applies when --source=issues."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Per-task timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max retry attempts per failed task (default: 2)",
    )
    parser.add_argument(
        "--max-fix-attempts",
        type=int,
        default=MAX_FIX_ATTEMPTS,
        help=(
            f"Max fix attempts per PR review cycle (default: {MAX_FIX_ATTEMPTS}). "
            f"When the agent is making progress (fewer errors each round), up to {EXTRA_FIX_ATTEMPTS_BUFFER} "
            "extra attempts are allowed beyond this limit."
        ),
    )
    parser.add_argument(
        "--log-dir",
        default="./logs",
        help="Directory for worker log files and status.json (default: ./logs)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the execution plan without spawning workers (default: False)",
    )
    parser.add_argument(
        "--decompose",
        action="store_true",
        default=False,
        help=(
            "Before fetching tasks, decompose vague agent-ready issues into "
            "sub-tasks using the DecomposerAgent (only applies when --source=issues)"
        ),
    )
    parser.add_argument(
        "--architect",
        action="store_true",
        default=False,
        help=(
            "Before spawning each worker, run ArchitectAgent with Claude Opus to "
            "design a detailed implementation spec and prepend it to the task "
            "description."
        ),
    )
    parser.add_argument(
        "--max-priority",
        type=int,
        default=None,
        help=(
            "Stop processing after completing this priority level "
            "(e.g. --max-priority 2 processes priority:1 and priority:2, skips priority:3)"
        ),
    )
    parser.add_argument(
        "--generate-claude-md",
        action="store_true",
        default=False,
        help=(
            "Auto-generate a CLAUDE.md in the target repo before starting tasks. "
            "Skipped if CLAUDE.md already exists."
        ),
    )
    parser.add_argument(
        "--notify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Send a desktop notification when orchestration completes (default: True)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        asyncio.run(orchestrate(
            plan_path=args.plan,
            max_workers=args.max_workers,
            repo_path=args.repo_path,
            timeout=args.timeout,
            source=args.source,
            label=args.label,
            issue_number=args.issue,
            max_retries=args.max_retries,
            max_fix_attempts=args.max_fix_attempts,
            log_dir=args.log_dir,
            dry_run=args.dry_run,
            decompose=args.decompose,
            architect=args.architect,
            max_priority=args.max_priority,
            generate_claude_md_flag=args.generate_claude_md,
            notify=args.notify,
        ))
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted![/bold red] Cancelling active workers…")
        log.warning("KeyboardInterrupt — shutting down")
        sys.exit(130)


if __name__ == "__main__":
    main()
