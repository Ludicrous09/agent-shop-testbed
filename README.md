# Agent Shop 🤖🏭

An autonomous multi-agent development pipeline powered by [Claude Code](https://docs.claude.com). Create a GitHub Issue, add the `agent-ready` label, and agents handle the rest — writing code, creating PRs, reviewing, fixing feedback, and merging.

## How It Works
```
GitHub Issue (agent-ready) → Worker Agent → PR → Review Agent → Fix Agent → Merge → Issue Closed
```

1. **You create an issue** describing what to build (or use the issue template)
2. **Worker agent** spins up an isolated git worktree, writes code and tests, commits, pushes, and opens a PR
3. **Review agent** reads the diff, posts a code review with verdict (approve/request changes)
4. **Fix agent** (if needed) addresses review feedback, commits fixes with linked references
5. **Review agent** re-reviews (up to 2 fix cycles)
6. **Auto-merge** squash merges the PR and closes the issue

Each step uses Claude Code in headless mode (`claude -p`) with restricted tool access for safety.

## Quick Start

### Prerequisites

- [Claude Code](https://docs.claude.com) CLI installed and authenticated
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated
- Python 3.10+
- Git

### Setup
```bash
git clone https://github.com/Ludicrous09/agent-shop-testbed.git
cd agent-shop-testbed

# Install Agent Shop as a package. It is a separate repository — this
# testbed no longer carries a copy of it.
python -m venv .venv
source .venv/bin/activate
pip install git+https://github.com/Ludicrous09/agent-shop.git

# Create required labels (one-time)
gh label create agent-ready --color 0E8A16 --description "Ready for agent to work on"
gh label create agent-failed --color D93F0B --description "Agent failed to complete"
gh label create agent-created --color C5DEF5 --description "PR created by agent"
gh label create priority:1 --color B60205 --description "Highest priority"
gh label create priority:2 --color FBCA04 --description "Medium priority"
gh label create priority:3 --color 0075CA --description "Low priority"
```

### Run from GitHub Issues
```bash
# Create an issue with the agent-ready label, then:
agent-shop --source issues --repo-path .
```

### Run from PLAN.yaml
```bash
agent-shop --plan PLAN.yaml --repo-path .
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source` | `plan` | Task source: `plan` (PLAN.yaml) or `issues` (GitHub Issues) |
| `--plan` | `PLAN.yaml` | Path to plan file (when source=plan) |
| `--label` | `agent-ready` | GitHub label to filter issues (when source=issues) |
| `--max-workers` | `2` | Maximum parallel worker agents |
| `--timeout` | `600` | Per-task timeout in seconds |

## Issue Format

Use the built-in issue template (🤖 Agent Task) or write issues manually:
```markdown
### Description

Add a `foo(x: int) -> str` function to src/bar.py that converts
integers to their English word representation. Handle negative numbers.
Add comprehensive tests in tests/test_bar.py.

### Files

- src/bar.py
- tests/test_bar.py

### Depends on

#5, #6

### Max turns

40
```

**Fields:**
- **Description** (required) — what to build, be specific about signatures and behavior
- **Files** (optional) — files that will be created/modified, used for conflict detection
- **Depends on** (optional) — issue numbers that must complete first
- **Max turns** (optional) — Claude Code turn limit (default: 50)

Priority is set via labels: `priority:1` (high), `priority:2` (medium), `priority:3` (low).

## PLAN.yaml Format
```yaml
tasks:
  - id: task-001
    title: Add authentication module
    description: |
      Create auth module at src/auth.py with JWT token generation,
      password hashing with bcrypt, and login/logout functions.
      Add tests in tests/test_auth.py.
    files_touched:
      - src/auth.py
      - tests/test_auth.py
    depends_on: []
    priority: 1
    max_turns: 60
    model: sonnet
```

## Architecture

This repository is the **target**: a small Python library that Agent Shop works
on. Agent Shop itself lives in
[Ludicrous09/agent-shop](https://github.com/Ludicrous09/agent-shop) and is
installed as a package.

```
src/                  # The code agents modify
tests/                # The suite they must keep green
PLAN.yaml             # Task source for --source plan
.agentshop.yaml       # Per-repo config: verify commands, labels, auto_merge
```

Until 2026-09-01 a copy of Agent Shop was committed here under `agent-shop/`,
kept in sync by a `sync.sh` that copied `*.py` between repositories. That copy
was six months stale by the end, and the tracebacks on
[agent-shop#111](https://github.com/Ludicrous09/agent-shop/issues/111) came from
it. It was removed once Agent Shop became pip-installable
([agent-shop#330](https://github.com/Ludicrous09/agent-shop/issues/330)).

### Worker Isolation

Each worker agent runs in an isolated [git worktree](https://git-scm.com/docs/git-worktree) at `/tmp/agent-worktrees/`. This means:
- Workers can't interfere with each other's files
- Workers can't modify the main branch directly
- Each worker gets a clean copy branched from latest `main`
- Worktrees are cleaned up automatically after each task

### Dependency Resolution

Tasks specify `depends_on` (other task IDs) and `files_touched`. The orchestrator:
- Only starts a task when all dependencies are completed
- Prevents parallel execution of tasks that touch the same files
- Pulls latest `main` before creating each worktree (avoids merge conflicts)
- Auto-merges approved PRs before starting dependent tasks

### Review Pipeline

The review agent sends the full PR diff + file contents to Claude and asks for a structured review:
- **APPROVE** — code is correct, tests pass, ready to merge
- **REQUEST_CHANGES** — found bugs, missing tests, or real issues

If changes are requested, the fix agent:
1. Reads the review comments
2. Checks out the PR branch in a worktree
3. Fixes each issue
4. Commits and pushes
5. Posts a comment linking to the fix commit

The review agent re-reviews (up to 2 fix cycles).

## Cost Management

Built for [Claude Max](https://claude.ai) subscription (5x plan):
- All worker/reviewer/fixer agents use the subscription (no extra cost)
- Model defaults to `sonnet` for workers (faster, cheaper if using API)
- Configurable per-task via `model` field in PLAN.yaml

## Limitations

- **No parallel file editing** — tasks touching the same files run sequentially
- **Review comments are PR-level** — not inline on specific lines (GitHub API limitation for self-owned PRs)
- **No automatic retry** on worker failures (task marked as failed)
- **Single repo** — orchestrator operates on one repo at a time

## License

MIT
