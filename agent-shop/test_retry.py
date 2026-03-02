"""Tests for retry logic in orchestrator and worker branch_suffix support."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from worker import Task, Worker, WorkerResult
from orchestrator import OrchestratorState, _cleanup_failed_branch, run_worker


# ---------------------------------------------------------------------------
# Worker branch_suffix tests
# ---------------------------------------------------------------------------


def make_task(task_id: str = "task-1", title: str = "Do Something") -> Task:
    return Task(
        id=task_id,
        title=title,
        description="A test task",
    )


def test_worker_default_branch_name() -> None:
    task = make_task(task_id="issue-1", title="Add retry logic")
    worker = Worker(repo_path="/tmp/repo", task=task, worker_id="w1")
    assert worker.branch == "agent/issue-1-add-retry-logic"
    assert str(worker.worktree_path).endswith("issue-1-add-retry-logic")


def test_worker_branch_suffix_retry_1() -> None:
    task = make_task(task_id="issue-1", title="Add retry logic")
    worker = Worker(repo_path="/tmp/repo", task=task, worker_id="w1", branch_suffix="-retry-1")
    assert worker.branch == "agent/issue-1-add-retry-logic-retry-1"
    assert str(worker.worktree_path).endswith("issue-1-add-retry-logic-retry-1")


def test_worker_branch_suffix_retry_2() -> None:
    task = make_task(task_id="issue-2", title="Fix bug")
    worker = Worker(repo_path="/tmp/repo", task=task, worker_id="w2", branch_suffix="-retry-2")
    assert worker.branch == "agent/issue-2-fix-bug-retry-2"


def test_worker_empty_branch_suffix_unchanged() -> None:
    task = make_task(task_id="issue-3", title="Some feature")
    worker_no_suffix = Worker(repo_path="/tmp/repo", task=task, worker_id="w3")
    worker_empty = Worker(repo_path="/tmp/repo", task=task, worker_id="w3", branch_suffix="")
    assert worker_no_suffix.branch == worker_empty.branch


# ---------------------------------------------------------------------------
# OrchestratorState retry tracking tests
# ---------------------------------------------------------------------------


def make_state(max_retries: int = 2) -> OrchestratorState:
    tasks = [make_task("task-1"), make_task("task-2")]
    return OrchestratorState(
        tasks=tasks,
        repo_path=Path("/tmp/repo"),
        timeout=600,
        log_dir=Path("/tmp/logs"),
        max_retries=max_retries,
    )


def test_state_initial_retry_counts() -> None:
    state = make_state()
    assert state.retry_counts == {}
    assert state.max_retries == 2


def test_state_max_retries_configurable() -> None:
    state = make_state(max_retries=5)
    assert state.max_retries == 5


def test_state_status_dict_includes_retries() -> None:
    state = make_state()
    state.retry_counts["task-1"] = 1
    d = state.status_dict()
    assert "retries" in d
    assert d["retries"] == {"task-1": 1}


def test_state_status_dict_retries_empty_by_default() -> None:
    state = make_state()
    d = state.status_dict()
    assert d["retries"] == {}


def test_state_status_dict_worker_includes_retry_count() -> None:
    state = make_state()
    mock_future = MagicMock()
    state.active_workers["task-1"] = mock_future
    state.retry_counts["task-1"] = 2
    d = state.status_dict()
    assert d["workers"]["task-1"]["retries"] == 2


def test_state_status_dict_reviewing_includes_retry_count() -> None:
    state = make_state()
    mock_future = MagicMock()
    state.review_futures["task-1"] = mock_future
    state.retry_counts["task-1"] = 1
    d = state.status_dict()
    assert d["workers"]["task-1"]["retries"] == 1


# ---------------------------------------------------------------------------
# _cleanup_failed_branch tests
# ---------------------------------------------------------------------------


@patch("orchestrator.subprocess.run")
def test_cleanup_failed_branch_deletes_remote_and_local(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0)
    _cleanup_failed_branch(Path("/tmp/repo"), "agent/task-1-some-task")
    assert mock_run.call_count == 2
    calls = [str(call) for call in mock_run.call_args_list]
    assert any("--delete" in c for c in calls)
    assert any("-D" in c for c in calls)


@patch("orchestrator.subprocess.run")
def test_cleanup_failed_branch_logs_warning_on_remote_failure(mock_run: MagicMock) -> None:
    # First call (remote delete) fails, second call (local delete) succeeds
    mock_run.side_effect = [
        MagicMock(returncode=1, stderr="remote not found"),
        MagicMock(returncode=0),
    ]
    # Should not raise — just logs warnings
    _cleanup_failed_branch(Path("/tmp/repo"), "agent/task-1-some-task")
    assert mock_run.call_count == 2


@patch("orchestrator.subprocess.run")
def test_cleanup_failed_branch_logs_warning_on_local_failure(mock_run: MagicMock) -> None:
    mock_run.side_effect = [
        MagicMock(returncode=0),
        MagicMock(returncode=1, stderr="branch not found"),
    ]
    _cleanup_failed_branch(Path("/tmp/repo"), "agent/task-1-some-task")
    assert mock_run.call_count == 2


# ---------------------------------------------------------------------------
# Retry integration: orchestrate loop behaviour
# ---------------------------------------------------------------------------


def make_worker_result(
    task_id: str,
    success: bool,
    branch: str = "agent/task-1-do-something",
    error: str | None = None,
) -> WorkerResult:
    return WorkerResult(
        task_id=task_id,
        branch=branch,
        success=success,
        error=error,
    )


def test_run_worker_passes_branch_suffix() -> None:
    """run_worker creates Worker with the given branch_suffix."""
    task = make_task("issue-5", "My feature")
    state = make_state()
    state.repo_path = Path("/tmp/repo")

    from concurrent.futures import ThreadPoolExecutor

    with patch("orchestrator.Worker") as MockWorker:
        mock_worker_instance = MagicMock()
        mock_worker_instance.run.return_value = make_worker_result("issue-5", True)
        MockWorker.return_value = mock_worker_instance

        executor = ThreadPoolExecutor(max_workers=1)
        asyncio.run(run_worker(executor, state, task, 1, branch_suffix="-retry-1"))

        MockWorker.assert_called_once_with(
            repo_path=state.repo_path,
            task=task,
            worker_id="worker-1",
            timeout=state.timeout,
            branch_suffix="-retry-1",
            log_dir=state.log_dir,
        )
        executor.shutdown(wait=False)
