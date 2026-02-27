"""Worker module that wraps Claude Code headless mode with git worktree isolation."""

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class WorkerError(Exception):
    """Raised when a worker encounters an unrecoverable error."""


@dataclass
class Task:
    id: str
    title: str
    description: str
    files_touched: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    priority: int = 1
    max_turns: int = 50
    model: str = "sonnet"


@dataclass
class WorkerResult:
    task_id: str
    branch: str
    success: bool
    pr_url: str | None = None
    pr_number: int | None = None
    error: str | None = None
    claude_output: str | None = None
    files_changed: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return 0.0


class Worker:
    WORKTREE_BASE = Path("/tmp/agent-worktrees/")

    def __init__(
        self,
        repo_path: str | Path,
        task: Task,
        worker_id: str,
        timeout: int = 600,
        log_dir: str | Path | None = None,
        branch_suffix: str = "",
    ):
        self.repo_path = Path(repo_path).resolve()
        self.task = task
        self.worker_id = worker_id
        self.timeout = timeout
        self.log_dir = (
            Path(log_dir) if log_dir else self.repo_path / "logs"
        )
        slug = self._slugify(task.title)
        self.branch = f"agent/{task.id}-{slug}{branch_suffix}"
        self.worktree_path = (
            self.WORKTREE_BASE / f"{task.id}-{slug}{branch_suffix}"
        )
        self._log_lines: list[str] = []

    def run(self) -> WorkerResult:
        started_at = datetime.now(timezone.utc)
        result = WorkerResult(
            task_id=self.task.id,
            branch=self.branch,
            success=False,
            started_at=started_at,
        )
        try:
            self._setup_worktree()
            claude_output = self._run_claude()
            result.claude_output = claude_output
            result.files_changed = self._verify_changes()
            self._push()
            pr_url, pr_number = self._create_pr(result)
            result.pr_url = pr_url
            result.pr_number = pr_number
            result.success = True
            logger.info("Task %s completed successfully: %s", self.task.id, pr_url)
        except Exception as e:
            result.error = str(e)
            logger.error("Task %s failed: %s", self.task.id, e)
        finally:
            result.finished_at = datetime.now(timezone.utc)
            self._cleanup()
            self._save_log(result)
        return result

    def _setup_worktree(self) -> None:
        logger.info(
            "Setting up worktree for task %s at %s", self.task.id, self.worktree_path
        )
        self.WORKTREE_BASE.mkdir(parents=True, exist_ok=True)

        # Pull latest main so the new worktree branches from up-to-date code
        logger.info("Pulling latest main before creating worktree")
        pull = subprocess.run(
            ["git", "pull"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if pull.returncode != 0:
            logger.warning("git pull on main failed: %s", pull.stderr[:300])

        # Clean up stale worktrees
        self._run_git(["worktree", "prune"])

        # Remove existing worktree path if present
        if self.worktree_path.exists():
            logger.warning("Removing existing worktree at %s", self.worktree_path)
            self._run_git(["worktree", "remove", "--force", str(self.worktree_path)])

        # Delete old branch if it exists
        try:
            self._run_git(["branch", "-D", self.branch])
            logger.info("Deleted existing branch %s", self.branch)
        except WorkerError:
            pass  # Branch doesn't exist, that's fine

        # Create new worktree with a new branch
        self._run_git(
            ["worktree", "add", "-b", self.branch, str(self.worktree_path), "main"]
        )
        logger.info("Created worktree on branch %s", self.branch)

    def _run_claude(self) -> str:
        prompt = self._build_prompt()
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            self.task.model,
            "--max-turns",
            str(self.task.max_turns),
            "--allowedTools",
            "Read,Write,Bash(git add:*),Bash(git commit:*),Bash(pytest:*),Bash(python:*),Bash(ruff:*)",
            "--dangerously-skip-permissions",
        ]
        logger.info(
            "Running claude for task %s (timeout=%ds)", self.task.id, self.timeout
        )
        self._log(f"$ {' '.join(cmd)}")

        try:
            env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
            proc = subprocess.run(
                cmd,
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise WorkerError(f"Claude timed out after {self.timeout}s") from e

        if proc.returncode != 0:
            self._log(f"claude stderr: {proc.stderr}")
            raise WorkerError(
                f"Claude exited with code {proc.returncode}: {proc.stderr[:500]}"
            )

        output = proc.stdout
        self._log(f"claude output length: {len(output)} chars")

        # Try to parse JSON output for cost/turn info
        try:
            data = json.loads(output)
            cost = data.get("cost_usd", "unknown")
            turns = data.get("num_turns", "unknown")
            logger.info("Task %s - cost: $%s, turns: %s", self.task.id, cost, turns)
            self._log(f"cost: ${cost}, turns: {turns}")
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Could not parse claude JSON output for task %s", self.task.id
            )

        return output

    def _verify_changes(self) -> list[str]:
        # Check for uncommitted changes and auto-commit them
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            logger.warning(
                "Found uncommitted changes, auto-committing for task %s", self.task.id
            )
            subprocess.run(["git", "add", "-A"], cwd=self.worktree_path, check=True)
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"[agent] chore: auto-commit remaining changes for {self.task.id}",
                ],
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
            )

        # Verify we have commits ahead of main
        log_result = subprocess.run(
            ["git", "log", "main..HEAD", "--oneline"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        if not log_result.stdout.strip():
            raise WorkerError("No commits ahead of main — nothing to push")

        self._log(f"Commits ahead of main:\n{log_result.stdout.strip()}")

        # Get list of changed files
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "main..HEAD"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        files_changed = [f for f in diff_result.stdout.strip().splitlines() if f]
        logger.info(
            "Task %s changed %d files: %s",
            self.task.id,
            len(files_changed),
            files_changed,
        )
        return files_changed

    def _push(self) -> None:
        logger.info("Pushing branch %s to origin", self.branch)
        result = subprocess.run(
            ["git", "push", "-u", "origin", self.branch, "--force"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise WorkerError(f"git push failed: {result.stderr[:500]}")
        self._log(f"Pushed branch {self.branch}")

    def _create_pr(self, result: WorkerResult) -> tuple[str, int]:
        title = f"[Agent] {self.task.title}"
        body = self._build_pr_body(result)

        # Try creating with agent-created label first
        pr_url = self._try_create_pr(title, body, label="agent-created")
        if not pr_url:
            # Retry without label if it doesn't exist
            logger.warning("Label 'agent-created' not found, retrying without label")
            pr_url = self._try_create_pr(title, body, label=None)

        if not pr_url:
            raise WorkerError("Failed to create PR")

        # Extract PR number from URL
        pr_number = int(pr_url.rstrip("/").split("/")[-1])
        logger.info("Created PR #%d: %s", pr_number, pr_url)
        return pr_url, pr_number

    def _try_create_pr(self, title: str, body: str, label: str | None) -> str | None:
        cmd = [
            "gh",
            "pr",
            "create",
            "--title",
            title,
            "--body",
            body,
            "--base",
            "main",
            "--head",
            self.branch,
        ]
        if label:
            cmd.extend(["--label", label])

        proc = subprocess.run(
            cmd,
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self._log(f"gh pr create failed: {proc.stderr}")
            return None
        return proc.stdout.strip()

    def _cleanup(self) -> None:
        if self.worktree_path.exists():
            logger.info("Cleaning up worktree at %s", self.worktree_path)
            try:
                self._run_git(
                    ["worktree", "remove", "--force", str(self.worktree_path)]
                )
            except WorkerError:
                logger.warning("Failed to remove worktree %s", self.worktree_path)

    def _build_prompt(self) -> str:
        desc = self.task.description
        title = self.task.title
        return (
            f"{desc}\n\n"
            f"After making your changes:\n"
            f"1. Run pytest to make sure all tests pass\n"
            f"2. Run ruff check . and fix any issues\n"
            f"3. Stage all changes with git add -A\n"
            f"4. Commit with message: [agent] feat: {title}\n"
            f"5. Do NOT push\n"
            f"6. Do NOT modify files in .github/"
        )

    def _build_pr_body(self, result: WorkerResult) -> str:
        files_section = (
            "\n".join(f"- `{f}`" for f in result.files_changed)
            if result.files_changed
            else "- None detected"
        )
        elapsed = f"{result.elapsed_seconds:.1f}s" if result.finished_at else "unknown"
        return (
            f"## Task\n\n"
            f"**ID:** `{self.task.id}`\n"
            f"**Title:** {self.task.title}\n\n"
            f"## Description\n\n"
            f"{self.task.description}\n\n"
            f"## Files Changed\n\n"
            f"{files_section}\n\n"
            f"## Details\n\n"
            f"- **Elapsed:** {elapsed}\n"
            f"- **Worker:** `{self.worker_id}`\n"
            f"- **Model:** `{self.task.model}`\n\n"
            f"---\n"
            f"*Automated PR created by agent-shop worker*"
        )

    def _save_log(self, result: WorkerResult) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{self.worker_id}-{self.task.id}.log"
        content = (
            f"task_id: {self.task.id}\n"
            f"worker_id: {self.worker_id}\n"
            f"branch: {self.branch}\n"
            f"success: {result.success}\n"
            f"elapsed: {result.elapsed_seconds:.1f}s\n"
            f"error: {result.error}\n"
            f"files_changed: {result.files_changed}\n"
            f"---\n" + "\n".join(self._log_lines)
        )
        log_path.write_text(content)
        logger.info("Log saved to %s", log_path)

    def _log(self, message: str) -> None:
        self._log_lines.append(f"[{datetime.now(timezone.utc).isoformat()}] {message}")

    def _run_git(self, args: list[str]) -> str:
        cmd = ["git"] + args
        proc = subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise WorkerError(f"git {' '.join(args)} failed: {proc.stderr[:500]}")
        return proc.stdout

    @staticmethod
    def _slugify(text: str) -> str:
        slug = text.lower().strip()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug[:50]


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    task = Task(
        id="test-001",
        title="Add subtract function",
        description=(
            "Add a subtract(a: int, b: int) -> int function to src/utils.py that returns a - b. "
            "Also add tests for it in tests/test_utils.py following the existing test patterns."
        ),
        files_touched=["src/utils.py", "tests/test_utils.py"],
    )

    import os

    repo = os.environ.get("REPO_PATH", os.getcwd())
    worker = Worker(
        repo_path=repo,
        task=task,
        worker_id="test-worker-1",
    )

    result = worker.run()
    if result.success:
        print(f"SUCCESS: PR {result.pr_url} (#{result.pr_number})")
        print(f"  Branch: {result.branch}")
        print(f"  Files: {result.files_changed}")
        print(f"  Elapsed: {result.elapsed_seconds:.1f}s")
    else:
        print(f"FAILED: {result.error}")
