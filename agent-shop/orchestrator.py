"""Orchestrator: run agent workers in parallel according to a PLAN.yaml."""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_manager import load_tasks, get_ready_tasks
from worker import Task, Worker, WorkerResult
from reviewer import ReviewAgent
from fixer import FixAgent
from issue_source import IssueSource
from decomposer import DecomposerAgent, is_vague
from conflict_resolver import ConflictResolver

from rich.console import Console
from rich.live import Live
from rich.table import Table

log = logging.getLogger("orchestrator")
console = Console()


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

    table.caption = (
        f"Total: {state.total}  "
        f"Active: {state.active}  "
        f"Completed: {len(state.completed_ids)}  "
        f"Failed: {len(state.failed_ids)}  "
        f"| Elapsed: {_format_duration(total_elapsed)}  "
        f"Cost: {cost_summary}  "
        f"Prompts: {prompts_summary}"
    )
    return table


# ---------------------------------------------------------------------------
# Review / Fix / Merge pipeline
# ---------------------------------------------------------------------------

MAX_FIX_ATTEMPTS = 2


def _review_fix_merge_sync(repo_path: Path, result: WorkerResult) -> bool:
    """Blocking review/fix/merge cycle. Returns True on successful merge."""
    pr = result.pr_number
    assert pr is not None

    for attempt in range(MAX_FIX_ATTEMPTS + 1):
        log.info("Reviewing PR #%d (attempt %d/%d)", pr, attempt + 1, MAX_FIX_ATTEMPTS + 1)
        try:
            reviewer = ReviewAgent(repo_path=repo_path, pr_number=pr)
            review = reviewer.review()
        except Exception as exc:
            log.error("ReviewAgent failed for PR #%d: %s", pr, exc)
            return False

        if review.verdict == "approve":
            log.info("PR #%d approved — merging", pr)
            proc = subprocess.run(
                ["gh", "pr", "merge", str(pr), "--squash", "--delete-branch"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                # Check if the failure is due to merge conflicts
                combined = (proc.stderr + proc.stdout).lower()
                is_conflict = any(
                    kw in combined for kw in ("conflict", "not mergeable")
                )
                if is_conflict:
                    log.info(
                        "PR #%d merge failed due to conflicts — attempting auto-resolution",
                        pr,
                    )
                    resolver = ConflictResolver(repo_path=repo_path, pr_number=pr)
                    conflict_result = resolver.resolve()
                    if conflict_result.success:
                        log.info(
                            "Conflicts resolved in PR #%d (%d files) — retrying merge",
                            pr,
                            len(conflict_result.resolved_files),
                        )
                        retry_proc = subprocess.run(
                            ["gh", "pr", "merge", str(pr), "--squash", "--delete-branch"],
                            cwd=repo_path,
                            capture_output=True,
                            text=True,
                        )
                        if retry_proc.returncode == 0:
                            log.info("PR #%d merged after conflict resolution", pr)
                            subprocess.run(
                                ["git", "pull"],
                                cwd=repo_path,
                                capture_output=True,
                                text=True,
                            )
                            return True
                        log.error(
                            "gh pr merge failed again after conflict resolution for PR #%d: %s",
                            pr,
                            retry_proc.stderr[:500],
                        )
                    else:
                        log.error(
                            "Conflict resolution failed for PR #%d: %s",
                            pr,
                            conflict_result.error,
                        )
                else:
                    log.error(
                        "gh pr merge failed for PR #%d (code %d): %s",
                        pr,
                        proc.returncode,
                        proc.stderr[:500],
                    )
                return False

            log.info("PR #%d merged — pulling latest main", pr)
            pull = subprocess.run(
                ["git", "pull"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            if pull.returncode != 0:
                log.warning("git pull after merge failed: %s", pull.stderr[:300])

            return True

        # verdict == "request_changes"
        if attempt >= MAX_FIX_ATTEMPTS:
            log.error(
                "PR #%d still not approved after %d fix attempt(s) — marking failed",
                pr,
                MAX_FIX_ATTEMPTS,
            )
            return False

        log.info(
            "PR #%d needs changes — running FixAgent (fix %d/%d)",
            pr,
            attempt + 1,
            MAX_FIX_ATTEMPTS,
        )
        try:
            fixer = FixAgent(repo_path=repo_path, pr_number=pr)
            fix_result = fixer.fix()
        except Exception as exc:
            log.error("FixAgent raised for PR #%d: %s", pr, exc)
            return False

        if not fix_result.success:
            log.error("FixAgent failed for PR #%d: %s", pr, fix_result.error)
            return False

        log.info("Fix applied to PR #%d — re-reviewing", pr)

    return False  # unreachable but satisfies type checkers


async def run_review_fix_merge(
    executor: ThreadPoolExecutor,
    repo_path: Path,
    result: WorkerResult,
) -> bool:
    """Run _review_fix_merge_sync in a thread via run_in_executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, _review_fix_merge_sync, repo_path, result)


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

        if not wave_tasks:
            break

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
    estimated_prompts = len(tasks) * 5
    console.print()
    console.print(
        f"[bold]Estimated prompt usage:[/bold] "
        f"{len(tasks)} tasks × ~5 prompts/task = ~{estimated_prompts} prompts"
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
    )
    if local_result.returncode != 0:
        log.warning(
            "Failed to delete local branch %s: %s",
            branch,
            local_result.stderr[:300],
        )


async def run_worker(
    executor: ThreadPoolExecutor,
    state: OrchestratorState,
    task,
    worker_counter: int,
    branch_suffix: str = "",
) -> WorkerResult:
    """Run a single Worker.run() in a thread via run_in_executor."""
    worker = Worker(
        repo_path=state.repo_path,
        task=task,
        worker_id=f"worker-{worker_counter}",
        timeout=state.timeout,
        branch_suffix=branch_suffix,
        log_dir=state.log_dir,
    )
    loop = asyncio.get_running_loop()
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


async def orchestrate(
    plan_path: str,
    max_workers: int,
    repo_path: str,
    timeout: int,
    source: str = "plan",
    label: str = "agent-ready",
    max_retries: int = 2,
    log_dir: str = "./logs",
    dry_run: bool = False,
    decompose: bool = False,
) -> None:
    repo = Path(repo_path).resolve()
    # status.json stays local to the orchestrator's working directory,
    # not inside the (potentially external) target repo.
    status_path = Path("status.json")
    log_dir_path = Path(log_dir)

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
        log.info("Fetching tasks from GitHub Issues (label=%s, repo=%s)", label, repo)
        issue_source = IssueSource(repo_path=repo, label=label)
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
                            run_review_fix_merge(executor, repo, result)
                        )
                        state.review_futures[task_id] = rfm_future
                    elif result.success:
                        state.completed_ids.add(task_id)
                        log.info("Task %s completed (no PR)", task_id)
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
                                    executor, state, task_obj, worker_counter, branch_suffix
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
                                executor, state, task_obj, worker_counter, branch_suffix
                            )
                        )
                        state.active_workers[task_id] = retry_future
                        state.task_durations.pop(task_id, None)
                        state.task_start_times[task_id] = datetime.now(timezone.utc)
                    else:
                        state.failed_ids.add(task_id)
                        log.error("Task %s raised exception: %s", task_id, exc)

                write_status(state, status_path)
                live.update(build_table(state))

            # Check for completed review/fix/merge futures
            done_review_ids = [tid for tid, f in state.review_futures.items() if f.done()]

            for task_id in done_review_ids:
                future = state.review_futures.pop(task_id)
                try:
                    merged: bool = future.result()
                    if merged:
                        state.completed_ids.add(task_id)
                        log.info("Task %s merged successfully", task_id)
                        # Close GitHub issue if using issues source
                        if issue_source and task_id.startswith("issue-"):
                            pr_url = next((r.pr_url for r in state.results if r.task_id == task_id), None)
                            try:
                                issue_source.mark_complete(task_id, pr_url or "N/A")
                            except Exception as exc:
                                log.warning("Failed to close issue for %s: %s", task_id, exc)
                    else:
                        state.failed_ids.add(task_id)
                        log.error("Task %s failed review/fix/merge cycle", task_id)
                        if issue_source and task_id.startswith("issue-"):
                            try:
                                issue_source.mark_failed(task_id, "Review/fix/merge cycle failed")
                            except Exception as exc:
                                log.warning("Failed to mark issue failed for %s: %s", task_id, exc)
                except Exception as exc:
                    state.failed_ids.add(task_id)
                    log.error("Task %s review pipeline raised: %s", task_id, exc)

                write_status(state, status_path)
                live.update(build_table(state))

            # Are we done?
            all_settled = state.completed_ids | state.failed_ids
            if len(all_settled) == state.total:
                log.info("All tasks settled — exiting")
                break
            if state.active == 0 and state.queued == 0 and state.reviewing == 0:
                log.info("No active workers, nothing queued, no reviews pending — exiting")
                break

            # Spawn new workers for ready tasks
            ready = get_ready_tasks(
                tasks,
                state.completed_ids,
                state.active_files,
            )
            # Filter out already-active and failed tasks
            ready = [
                t for t in ready
                if t.id not in state.active_workers
                and t.id not in state.failed_ids
                and t.id not in state.review_futures
                and t.id not in state.completed_ids
            ]

            slots = max_workers - state.active
            for task in ready[:slots]:
                worker_counter += 1
                log.info("Spawning worker for task %s", task.id)
                state.active_files.update(task.files_touched)
                future = asyncio.ensure_future(
                    run_worker(executor, state, task, worker_counter)
                )
                state.active_workers[task.id] = future
                state.task_start_times[task.id] = datetime.now(timezone.utc)
                write_status(state, status_path)
                live.update(build_table(state))

            # Brief sleep to avoid busy-looping
            await asyncio.sleep(2)

    executor.shutdown(wait=False)

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
            max_retries=args.max_retries,
            log_dir=args.log_dir,
            dry_run=args.dry_run,
            decompose=args.decompose,
        ))
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted![/bold red] Cancelling active workers…")
        log.warning("KeyboardInterrupt — shutting down")
        sys.exit(130)


if __name__ == "__main__":
    main()
