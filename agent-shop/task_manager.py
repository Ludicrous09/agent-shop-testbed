"""Load and manage tasks from a PLAN.yaml file."""

import yaml
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))

from worker import Task


def load_tasks(plan_path: str | Path) -> list[Task]:
    """Read a YAML plan file and return a list of Task objects.

    The YAML must have a top-level 'tasks' key containing a list of task dicts.
    Each task dict must have: id, title, description, files_touched.
    Optional: depends_on (default []), priority (default 1),
              max_turns (default 50), model (default 'sonnet').

    Raises ValueError if task IDs are not unique or depends_on references
    a non-existent task ID.
    """
    plan_path = Path(plan_path)
    with open(plan_path) as f:
        data = yaml.safe_load(f)

    raw_tasks = data.get("tasks")
    if not raw_tasks:
        raise ValueError(f"No 'tasks' key found in {plan_path}")

    tasks = []
    seen_ids: set[str] = set()

    for entry in raw_tasks:
        task_id = entry["id"]
        if task_id in seen_ids:
            raise ValueError(f"Duplicate task ID: {task_id}")
        seen_ids.add(task_id)

        tasks.append(Task(
            id=task_id,
            title=entry["title"],
            description=entry["description"],
            files_touched=entry.get("files_touched", []),
            depends_on=entry.get("depends_on", []),
            priority=entry.get("priority", 1),
            max_turns=entry.get("max_turns", 50),
            model=entry.get("model", "sonnet"),
        ))

    # Validate depends_on references
    all_ids = {t.id for t in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep not in all_ids:
                raise ValueError(
                    f"Task '{task.id}' depends on '{dep}' which does not exist"
                )

    return tasks


def get_ready_tasks(
    tasks: list[Task],
    completed_ids: set[str],
    active_files: set[str],
) -> list[Task]:
    """Return tasks whose dependencies are met and files are not in use.

    A task is ready when:
    - It is not already completed
    - All its depends_on IDs are in completed_ids
    - None of its files_touched overlap with active_files

    Results are sorted by priority (lowest number = highest priority).
    """
    ready = []
    for task in tasks:
        if task.id in completed_ids:
            continue
        if not all(dep in completed_ids for dep in task.depends_on):
            continue
        if active_files & set(task.files_touched):
            continue
        ready.append(task)

    ready.sort(key=lambda t: t.priority)
    return ready
