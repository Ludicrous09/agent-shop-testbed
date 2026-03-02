"""Worker module that wraps Claude Code headless mode with git worktree isolation."""

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

_LARGE_PROMPT_THRESHOLD = 100 * 1024  # 100 KB


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
    cost_usd: float | None = None
    num_turns: int | None = None

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
            claude_output, parsed = self._run_claude()
            result.claude_output = claude_output
            self._enforce_file_scope()
            if parsed is not None:
                raw_cost = parsed.get("cost_usd")
                raw_turns = parsed.get("num_turns")
                result.cost_usd = float(raw_cost) if raw_cost is not None else None
                result.num_turns = int(raw_turns) if raw_turns is not None else None
            result.files_changed = self._verify_changes()
            self._rebase_before_push()
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
            timeout=120,
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

    def _run_claude(self) -> tuple[str, dict | None]:
        prompt = self._build_prompt()
        use_stdin = len(prompt.encode("utf-8")) > _LARGE_PROMPT_THRESHOLD

        cmd = [
            "claude",
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
        if use_stdin:
            stdin_input: str | None = prompt
        else:
            cmd += ["-p", prompt]
            stdin_input = None

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
                input=stdin_input,
            )
        except subprocess.TimeoutExpired as e:
            raise WorkerError(f"Claude timed out after {self.timeout}s") from e

        if proc.returncode != 0:
            logger.error(
                "Claude stderr for task %s: %s", self.task.id, proc.stderr[:500]
            )
            raise WorkerError(
                f"Claude exited with code {proc.returncode} — check server logs for details"
            )

        output = proc.stdout
        self._log(f"claude output length: {len(output)} chars")

        # Parse JSON output once for cost/turn info and return to caller
        parsed: dict | None = None
        try:
            parsed = json.loads(output)
            cost = parsed.get("cost_usd", "unknown")
            turns = parsed.get("num_turns", "unknown")
            logger.info("Task %s - cost: $%s, turns: %s", self.task.id, cost, turns)
            self._log(f"cost: ${cost}, turns: {turns}")
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Could not parse claude JSON output for task %s", self.task.id
            )

        return output, parsed

    @staticmethod
    def _path_is_authorized(filepath: str, allowed: set[str]) -> bool:
        """Return True if *filepath* is covered by any entry in *allowed*.

        Handles both exact matches and partial-path (suffix) matches so that
        an allowlist entry of ``approval.py`` authorises a changed file
        reported as ``bot/cogs/approval.py``, and vice-versa.
        """
        if filepath in allowed:
            return True
        for a in allowed:
            # allowlist entry is a suffix of the changed path
            # e.g. a="approval.py", filepath="bot/cogs/approval.py"
            if filepath.endswith("/" + a):
                return True
            # changed path is a suffix of the allowlist entry
            # e.g. a="bot/cogs/approval.py", filepath="approval.py"
            # NOTE: this direction may over-authorise when the allowlist entry
            # is longer than the changed path.  Any file sharing the same
            # basename (e.g. a root-level "approval.py") will be authorised by
            # an entry like "bot/cogs/approval.py".  The enforcement is
            # intentionally permissive here, but callers should be aware that
            # the reverse direction is weaker than a strict path check.
            if a.endswith("/" + filepath):
                return True
        return False

    def _enforce_file_scope(self) -> None:
        """Revert any files modified outside of task.files_touched."""
        if not self.task.files_touched:
            return

        allowed = set(self.task.files_touched)

        # Get all files changed relative to main (staged + unstaged + committed)
        diff_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        staged_result = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Also check committed changes vs main
        committed_result = subprocess.run(
            ["git", "diff", "--name-only", "main..HEAD"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        # Catch untracked files created but never staged
        untracked_result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        changed = set()
        for output in (
            diff_result.stdout,
            staged_result.stdout,
            committed_result.stdout,
            untracked_result.stdout,
        ):
            for line in output.strip().splitlines():
                f = line.strip()
                if f:
                    changed.add(f)

        unauthorized = {f for f in changed if not self._path_is_authorized(f, allowed)}
        if not unauthorized:
            return

        logger.warning(
            "Task %s modified unauthorized files (will revert): %s",
            self.task.id,
            sorted(unauthorized),
        )
        self._log(f"Reverting unauthorized files: {sorted(unauthorized)}")

        worktree_resolved = str(self.worktree_path.resolve())
        for filepath in sorted(unauthorized):
            # Guard against path traversal attacks from git output
            full_path = (self.worktree_path / filepath).resolve()
            if not str(full_path).startswith(worktree_resolved + os.sep):
                logger.warning("Path traversal blocked: %s", filepath)
                continue
            # Check whether the file exists in main
            cat_file = subprocess.run(
                ["git", "cat-file", "-e", f"main:{filepath}"],
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if cat_file.returncode == 0:
                # File exists in main — restore it to the main version.
                # NOTE: if the file was already committed on this branch, the
                # original commit remains visible in branch history. The revert
                # is recorded as a follow-up commit, producing an add-then-revert
                # pair in the PR diff. A stronger guarantee would require an
                # interactive rebase/squash to drop the offending commit entirely,
                # but that is not implemented here.
                revert = subprocess.run(
                    ["git", "checkout", "main", "--", filepath],
                    cwd=self.worktree_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if revert.returncode != 0:
                    logger.warning(
                        "Could not revert %s: %s", filepath, revert.stderr[:200]
                    )
            else:
                # File is new (not in main) — delete it from disk and index
                try:
                    full_path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Could not delete %s: %s", filepath, exc)
                subprocess.run(
                    ["git", "rm", "--cached", "-f", "--", filepath],
                    cwd=self.worktree_path,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

    def _verify_changes(self) -> list[str]:
        # Check for uncommitted changes and auto-commit them
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if status.stdout.strip():
            logger.warning(
                "Found uncommitted changes, auto-committing for task %s", self.task.id
            )
            subprocess.run(
                ["git", "add", "-A"], cwd=self.worktree_path, check=True, timeout=60
            )
            proc = subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    f"[agent] chore: auto-commit remaining changes for {self.task.id}",
                ],
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if proc.returncode != 0:
                raise WorkerError(f"Auto-commit failed: {proc.stderr[:300]}")

        # Verify we have commits ahead of main
        log_result = subprocess.run(
            ["git", "log", "main..HEAD", "--oneline"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=60,
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
            timeout=60,
        )
        files_changed = [f for f in diff_result.stdout.strip().splitlines() if f]
        logger.info(
            "Task %s changed %d files: %s",
            self.task.id,
            len(files_changed),
            files_changed,
        )
        return files_changed

    def _rebase_before_push(self) -> None:
        """Rebase onto origin/main before pushing to prevent merge conflicts."""
        logger.info(
            "Fetching origin/main for rebase before push (task %s)", self.task.id
        )

        fetch = subprocess.run(
            ["git", "fetch", "origin", "main"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        if fetch.returncode != 0:
            logger.warning("git fetch origin main failed: %s", fetch.stderr[:300])
            self._log(f"git fetch failed (will still push): {fetch.stderr[:200]}")
            return

        rebase = subprocess.run(
            ["git", "rebase", "origin/main"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )

        if rebase.returncode == 0:
            logger.info(
                "Rebase onto origin/main succeeded cleanly (task %s)", self.task.id
            )
            self._log("Rebase outcome: clean")
            return

        # Rebase had conflicts — abort and fall back to merge
        logger.warning(
            "Rebase had conflicts for task %s, aborting and trying merge", self.task.id
        )
        self._log("Rebase outcome: conflict — trying merge fallback")

        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )

        merge = subprocess.run(
            ["git", "merge", "origin/main"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )

        if merge.returncode == 0:
            logger.info("Merge fallback succeeded for task %s", self.task.id)
            self._log("Rebase outcome: conflict-resolved via merge")
            return

        # Merge also conflicted — delegate resolution to Claude
        logger.warning(
            "Merge also conflicted for task %s — delegating to Claude", self.task.id
        )

        conflict_files_result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        conflicted_files = [
            f for f in conflict_files_result.stdout.strip().splitlines() if f
        ]
        self._log(f"Conflicted files: {conflicted_files}")

        if conflicted_files:
            try:
                self._resolve_conflicts_with_claude(conflicted_files)
                self._log("Rebase outcome: conflict-resolved via Claude")
                return
            except Exception as e:
                logger.error(
                    "Claude conflict resolution failed for task %s: %s", self.task.id, e
                )
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=self.worktree_path,
                    capture_output=True,
                )
                self._log(
                    f"Rebase outcome: conflict resolution failed ({e})"
                    " — will push pre-merge HEAD (branch not rebased onto main)"
                )
        else:
            logger.error(
                "Could not determine conflicted files for task %s", self.task.id
            )
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=self.worktree_path,
                capture_output=True,
            )
            self._log(
                "Rebase outcome: conflict resolution failed (no conflicted files found)"
                " — will push pre-merge HEAD (branch not rebased onto main)"
            )

    def _resolve_conflicts_with_claude(self, conflicted_files: list[str]) -> None:
        """Use Claude to resolve merge conflicts in the given files."""
        files_list = ", ".join(conflicted_files)
        prompt = (
            "Resolve these merge conflicts. Keep all changes from both sides. "
            "The HEAD changes are your work, the incoming changes are from main.\n\n"
            f"Conflicted files: {files_list}\n\n"
            "After resolving all conflicts:\n"
            "1. Stage the resolved files with: git add -A\n"
            "2. Complete the merge with: git commit --no-edit\n"
        )

        use_stdin = len(prompt.encode("utf-8")) > _LARGE_PROMPT_THRESHOLD

        cmd = [
            "claude",
            "--output-format",
            "json",
            "--model",
            self.task.model,
            "--max-turns",
            "10",
            "--allowedTools",
            "Read,Write,Bash(git add:*),Bash(git commit:*),Bash(git diff:*)",
            "--dangerously-skip-permissions",
        ]
        if use_stdin:
            stdin_input: str | None = prompt
        else:
            cmd += ["-p", prompt]
            stdin_input = None

        logger.info(
            "Running Claude to resolve conflicts for task %s", self.task.id
        )
        self._log(f"Running Claude for conflict resolution on: {conflicted_files}")

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        proc = subprocess.run(
            cmd,
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
            input=stdin_input,
        )

        if proc.returncode != 0:
            logger.error(
                "Claude conflict resolution stderr for task %s: %s",
                self.task.id,
                proc.stderr[:500],
            )
            raise WorkerError(
                f"Claude conflict resolution exited with {proc.returncode} — check server logs for details"
            )

        # Verify no conflicts remain
        remaining = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=self.worktree_path,
            capture_output=True,
            text=True,
        )
        remaining_files = [f for f in remaining.stdout.strip().splitlines() if f]
        if remaining_files:
            raise WorkerError(
                f"Conflicts remain after Claude resolution: {remaining_files}"
            )

        logger.info(
            "Claude successfully resolved conflicts for task %s", self.task.id
        )

    def _push(self) -> None:
        logger.info("Pushing branch %s to origin", self.branch)
        delays = [5, 15, 45]
        last_stderr = ""
        for attempt, delay in enumerate(delays, start=1):
            result = subprocess.run(
                ["git", "push", "-u", "origin", self.branch, "--force"],
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                self._log(f"Pushed branch {self.branch}")
                return
            last_stderr = result.stderr[:500]
            if attempt < len(delays):
                logger.warning(
                    "git push failed (attempt %d/%d), retrying in %ds: %s",
                    attempt,
                    len(delays),
                    delay,
                    last_stderr,
                )
                time.sleep(delay)
        logger.error("git push failed for task %s: %s", self.task.id, last_stderr)
        raise WorkerError(f"git push failed after {len(delays)} attempts — check server logs for details")

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

        delays = [5, 15, 45]
        for attempt, delay in enumerate(delays, start=1):
            proc = subprocess.run(
                cmd,
                cwd=self.worktree_path,
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
            stderr = proc.stderr
            # Non-transient failure: label doesn't exist — signal caller to retry without label
            if label and "could not find label" in stderr.lower():
                self._log(f"gh pr create failed: {stderr}")
                return None
            # Non-transient failure: PR already exists
            if "already exists" in stderr.lower():
                self._log(f"gh pr create failed: {stderr}")
                return None
            if attempt < len(delays):
                logger.warning(
                    "gh pr create failed (attempt %d/%d), retrying in %ds: %s",
                    attempt,
                    len(delays),
                    delay,
                    stderr[:500],
                )
                time.sleep(delay)
            else:
                self._log(f"gh pr create failed: {stderr}")
        return None

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
        file_scope = ""
        if self.task.files_touched:
            files_list = ", ".join(self.task.files_touched)
            file_scope = (
                f"You MUST only create or modify these files: {files_list}. "
                f"Do not touch any other files.\n\n"
            )

        task_body = (
            f"{file_scope}"
            f"{desc}\n\n"
            f"After making your changes:\n"
            f"1. Run pytest to make sure all tests pass\n"
            f"2. Run ruff check . and fix any issues\n"
            f"3. Stage all changes with git add -A\n"
            f"4. Commit with message: [agent] feat: {title}\n"
            f"5. Do NOT push\n"
            f"6. Do NOT modify files in .github/"
        )

        # Read CLAUDE.md from the worktree root if it exists
        claude_md_content: str = ""
        claude_md_path = self.worktree_path / "CLAUDE.md"
        if claude_md_path.exists():
            try:
                claude_md_content = claude_md_path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Could not read CLAUDE.md: %s", exc)

        # Read .claude/settings.json if it exists and include relevant settings
        settings_section: str = ""
        settings_path = self.worktree_path / ".claude" / "settings.json"
        if settings_path.exists():
            try:
                settings_data = json.loads(
                    settings_path.read_text(encoding="utf-8")
                )
                if settings_data:
                    settings_section = (
                        "\n\nProject settings (.claude/settings.json):\n"
                        + json.dumps(settings_data, indent=2)
                    )
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("Could not read .claude/settings.json: %s", exc)

        if claude_md_content:
            context = (
                f"The following is the project context from CLAUDE.md:\n"
                f"{claude_md_content}"
                f"{settings_section}"
            )
            return f"{context}\n\n---\n\nYour task:\n{task_body}"

        if settings_section:
            return f"{settings_section.lstrip()}\n\n---\n\nYour task:\n{task_body}"

        return task_body

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
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired as e:
            raise WorkerError(f"git {' '.join(args)} timed out after 120s") from e
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
