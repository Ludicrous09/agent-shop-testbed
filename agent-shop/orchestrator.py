"""Orchestrator: run agent workers in parallel according to a PLAN.yaml."""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from task_manager import load_tasks, get_ready_tasks
from worker import Worker, WorkerResult
from reviewer import ReviewAgent
from fixer import FixAgent

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

    def __init__(self, tasks, repo_path: Path, timeout: int):
        self.tasks = tasks
        self.repo_path = repo_path
        self.timeout = timeout

        self.completed_ids: set[str] = set()
        self.failed_ids: set[str] = set()
        self.active_workers: dict[str, asyncio.Future] = {}  # task_id -> future
        self.review_futures: dict[str, asyncio.Future] = {}  # task_id -> review/fix/merge future
        self.active_files: set[str] = set()
        self.results: list[WorkerResult] = []

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
                **{tid: {"status": "running"} for tid in self.active_workers},
                **{tid: {"status": "reviewing"} for tid in self.review_futures},
            },
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

def build_table(state: OrchestratorState) -> Table:
    table = Table(title="Orchestrator Status", expand=True)
    table.add_column("Task ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Priority", justify="center")
    table.add_column("Status", justify="center")

    task_map = {t.id: t for t in state.tasks}
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

        table.add_row(task.id, task.title, str(task.priority), status)

    table.caption = (
        f"Total: {state.total}  "
        f"Active: {state.active}  "
        f"Completed: {len(state.completed_ids)}  "
        f"Failed: {len(state.failed_ids)}"
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
# Core loop
# ---------------------------------------------------------------------------

async def run_worker(
    executor: ThreadPoolExecutor,
    state: OrchestratorState,
    task,
    worker_counter: int,
) -> WorkerResult:
    """Run a single Worker.run() in a thread via run_in_executor."""
    worker = Worker(
        repo_path=state.repo_path,
        task=task,
        worker_id=f"worker-{worker_counter}",
        timeout=state.timeout,
    )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, worker.run)


async def orchestrate(
    plan_path: str,
    max_workers: int,
    repo_path: str,
    timeout: int,
) -> None:
    repo = Path(repo_path).resolve()
    status_path = repo / "agent-shop" / "status.json"

    log.info("Loading plan from %s", plan_path)
    tasks = load_tasks(plan_path)
    log.info("Loaded %d tasks", len(tasks))

    state = OrchestratorState(tasks, repo, timeout)
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
                        state.failed_ids.add(task_id)
                        log.error("Task %s failed: %s", task_id, result.error)
                except Exception as exc:
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
                    else:
                        state.failed_ids.add(task_id)
                        log.error("Task %s failed review/fix/merge cycle", task_id)
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
                if t.id not in state.active_workers and t.id not in state.failed_ids
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
        "--timeout",
        type=int,
        default=600,
        help="Per-task timeout in seconds (default: 600)",
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
        ))
    except KeyboardInterrupt:
        console.print("\n[bold red]Interrupted![/bold red] Cancelling active workers…")
        log.warning("KeyboardInterrupt — shutting down")
        sys.exit(130)


if __name__ == "__main__":
    main()
