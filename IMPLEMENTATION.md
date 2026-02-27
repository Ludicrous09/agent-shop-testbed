# Agent Shop — Implementation Plan & Status

**Project:** Autonomous Multi-Agent Development Pipeline
**Repo:** [Ludicrous09/agent-shop-testbed](https://github.com/Ludicrous09/agent-shop-testbed)
**Runtime:** WSL Ubuntu on local machine
**Language:** Python 3.12 with venv
**Subscription:** Claude Max 5x ($100/mo)

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    YOU (Jason)                            │
│         Create Issues → Label "agent-ready" → Merge      │
└──────────────┬───────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────┐
│            ORCHESTRATOR (agent-shop/orchestrator.py)      │
│                                                          │
│  • Reads GitHub Issues (--source issues) or PLAN.yaml    │
│  • Resolves dependencies + file conflict detection       │
│  • Spawns Claude Code workers in isolated worktrees      │
│  • Runs review/fix/merge pipeline after each PR          │
│  • Auto-merges approved PRs, closes issues               │
│  • Rich terminal dashboard with live status              │
│  • Writes status.json for monitoring                     │
└──────┬──────────────┬──────────────┬─────────────────────┘
       │              │              │
       ▼              ▼              ▼
┌────────────┐ ┌────────────┐ ┌──────────────┐
│  Worker    │ │  Worker    │ │ Review Agent │
│  Agent     │ │  Agent     │ │              │
│ (headless) │ │ (headless) │ │ Reviews PR   │
│ branch/A   │ │ branch/B   │ │ Posts verdict│
│ Code+Test  │ │ Code+Test  │ │ Fix→Re-review│
│ Commit+PR  │ │ Commit+PR  │ │ Auto-merge   │
└─────┬──────┘ └─────┬──────┘ └──────┬───────┘
      │              │               │
      ▼              ▼               ▼
┌──────────────────────────────────────────────────────────┐
│                    GitHub                                 │
│  • Feature branches with PRs                             │
│  • Labels: agent-ready, agent-failed, agent-created      │
│  • Issue template for structured task creation           │
│  • Auto-closed issues on successful merge                │
└──────────────────────────────────────────────────────────┘
```

---

## Pipeline Flow

```
GitHub Issue (agent-ready) → Worker → PR → Review → Fix (if needed) → Re-review → Merge → Issue Closed
```

1. **Issue created** with `agent-ready` label (via template or manually)
2. **Orchestrator** fetches open `agent-ready` issues, resolves dependencies
3. **Worker agent** creates isolated git worktree, writes code + tests, commits, pushes, opens PR
4. **Review agent** reads full diff + file contents, posts structured code review
5. **Fix agent** (if changes requested) addresses feedback, commits fixes with linked references
6. **Review agent** re-reviews (up to 2 fix cycles)
7. **Auto-merge** squash merges approved PR, pulls latest main
8. **Issue closed** with comment linking to merged PR
9. **Next dependent task** starts from updated main (no merge conflicts)

---

## Phase Status

### Phase 1: Foundation ✅ COMPLETE

Single Claude Code worker completing tasks end-to-end.

**What was built:**
- `agent-shop/worker.py` — Core worker module
  - Git worktree isolation at `/tmp/agent-worktrees/`
  - Claude Code headless mode (`claude -p`) with `--dangerously-skip-permissions`
  - Restricted tool access via `--allowedTools`
  - Auto-commit fallback if Claude skips git commands
  - PR creation via `gh pr create`
  - Automatic worktree cleanup
  - Per-worker log files in `agent-shop/logs/`

**Key learnings:**
- `--dangerously-skip-permissions` is required for headless mode (Claude asks for file write approval otherwise)
- Action-oriented prompts work better than structured task descriptions
- Model forced to `sonnet` for workers (5x throughput vs Opus)
- Worker timeout of 600s prevents runaway agents

**Proven with:** Manual test (PR #1), automated test (PR #2)

### Phase 2: Orchestrator ✅ COMPLETE

Parallel worker orchestration with dependency resolution.

**What was built:**
- `agent-shop/orchestrator.py` — Async main loop
  - `asyncio` + `ThreadPoolExecutor` for parallel worker execution
  - Dependency resolution (tasks specify `depends_on`)
  - File conflict detection (tasks specify `files_touched`)
  - Rich live table showing task status (queued/running/reviewing/completed/failed)
  - `status.json` written after every state change
  - CLI args: `--plan`, `--source`, `--label`, `--max-workers`, `--timeout`
  - Graceful `KeyboardInterrupt` handling
- `agent-shop/task_manager.py` — PLAN.yaml parser
  - Validates unique task IDs and dependency references
  - `get_ready_tasks()` returns tasks with met dependencies and no file conflicts

**Proven with:** 3-task dependency chain (PRs #3-5), re-run after fixes (PRs #6, #8)

### Phase 3: Review & Fix Pipeline ✅ COMPLETE

Automated code review with feedback loop.

**What was built:**
- `agent-shop/reviewer.py` — Code review agent
  - Reads PR diff + full file contents from PR branch
  - Sends to Claude for structured JSON review
  - Posts verdict as PR comment with `[REVIEW: APPROVE]` or `[REVIEW: REQUEST_CHANGES]`
  - Inline comments listed with severity (ERROR/WARNING/SUGGESTION)
  - Falls back to comment-based reviews (GitHub API doesn't allow self-approval)
- `agent-shop/fixer.py` — Review feedback fixer
  - Reads latest `REQUEST_CHANGES` comment from PR
  - Creates worktree from PR branch, runs Claude to fix issues
  - Pushes fix commit, posts reply comment with commit link
  - Up to 2 fix attempts before marking task failed
- Integrated into orchestrator: Worker → Review → Fix → Re-review → Merge

**Real example (PR #9):**
1. Worker created string_utils.py with a truncate function
2. Review agent found a **real bug** — negative index when `max_length < len(suffix)`
3. Fix agent fixed the bug and added missing test cases
4. Re-review approved
5. Auto-merged

### Phase 4: GitHub Issues as Task Source ✅ COMPLETE

**What was built:**
- `agent-shop/issue_source.py` — GitHub Issues parser
  - Fetches open issues with configurable label (default: `agent-ready`)
  - Parses structured issue body: Description, Files, Depends on, Max turns
  - Supports both freeform markdown and GitHub issue template format
  - Priority from labels (`priority:1`, `priority:2`, `priority:3`)
  - `mark_complete()` — posts comment with PR link, closes issue
  - `mark_failed()` — posts error comment, adds `agent-failed` label
- `.github/ISSUE_TEMPLATE/agent-task.yml` — Structured issue template
- GitHub labels created: `agent-ready`, `agent-failed`, `agent-created`, `priority:1-3`
- Wired into orchestrator with `--source issues` flag

**Proven with:** Issues #10, #12 → PRs #11, #13 (both auto-merged, issues auto-closed)

---

## File Structure

```
agent-shop-testbed/
├── .github/
│   └── ISSUE_TEMPLATE/
│       └── agent-task.yml          # Structured issue template
├── agent-shop/
│   ├── orchestrator.py             # Main loop — spawns workers, manages state
│   ├── worker.py                   # Claude Code headless worker
│   ├── reviewer.py                 # Code review agent
│   ├── fixer.py                    # Review feedback fixer
│   ├── task_manager.py             # PLAN.yaml parser + dependency resolver
│   ├── issue_source.py             # GitHub Issues as task source
│   ├── status.json                 # Live orchestration state
│   ├── .venv/                      # Python virtual environment
│   └── logs/                       # Per-worker execution logs
├── src/                            # Application code (built by agents)
│   ├── utils.py                    # add, subtract, multiply, divide, power
│   ├── calculator.py               # Calculator class wrapping utils
│   ├── string_utils.py             # reverse, is_palindrome, word_count, truncate
│   ├── stats.py                    # mean, median, mode, std_dev
│   └── conversions.py              # Temperature, distance, weight conversions
├── tests/                          # Test suite (built by agents)
│   ├── test_utils.py
│   ├── test_calculator.py
│   ├── test_string_utils.py
│   ├── test_stats.py
│   └── test_conversions.py
├── PLAN.yaml                       # Task definitions (alternative to issues)
├── README.md                       # Project documentation
└── requirements.txt                # pytest, ruff
```

---

## Usage

### From GitHub Issues (recommended)

```bash
# 1. Create an issue with the "agent-ready" label (use the template)
# 2. Run the orchestrator
cd ~/code/personal/agent-shop-testbed
source agent-shop/.venv/bin/activate
python agent-shop/orchestrator.py --source issues
```

### From PLAN.yaml

```bash
python agent-shop/orchestrator.py --plan PLAN.yaml
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source` | `plan` | Task source: `plan` or `issues` |
| `--plan` | `PLAN.yaml` | Path to plan file |
| `--label` | `agent-ready` | GitHub label filter for issues |
| `--max-workers` | `2` | Maximum parallel workers |
| `--timeout` | `600` | Per-task timeout (seconds) |

### Run Individual Components

```bash
# Review a specific PR
python agent-shop/reviewer.py --pr 13

# Fix review feedback on a PR
python agent-shop/fixer.py --pr 13

# List available agent-ready issues
python agent-shop/issue_source.py
```

---

## Issue Format

Use the built-in template (🤖 Agent Task) or write manually:

```markdown
### Description

Add a `foo(x: int) -> str` function to src/bar.py that...
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
- **Description** (required) — what to build, be specific
- **Files** (optional) — created/modified files, used for conflict detection
- **Depends on** (optional) — issue numbers that must complete first
- **Max turns** (optional) — Claude turn limit (default: 50)
- **Priority** — set via labels: `priority:1` (high), `priority:2`, `priority:3` (low)

---

## PLAN.yaml Format

```yaml
tasks:
  - id: task-001
    title: Add authentication module
    description: |
      Create auth module at src/auth.py with JWT tokens,
      login/logout, password hashing. Add tests.
    files_touched:
      - src/auth.py
      - tests/test_auth.py
    depends_on: []
    priority: 1
    max_turns: 60
    model: sonnet
```

---

## Cost Management

**Primary: Max 5x Subscription ($100/mo)**
- All worker/reviewer/fixer agents use subscription (no extra cost)
- Workers forced to `sonnet` model for throughput
- ~225 prompts per 5-hour rolling window

**Fallback: API key (not yet implemented)**
- For overnight batch runs when subscription quota is exhausted
- Hard limits: $30/month workspace cap, $2/day budget
- Set `ANTHROPIC_API_KEY` env var to enable

---

## Known Issues & Limitations

- **No parallel file editing** — tasks touching the same files run sequentially
- **Review comments are PR-level** — not inline (GitHub API doesn't allow self-approval)
- **No automatic retry** on worker failures (task marked as failed)
- **Single repo** — orchestrator operates on one repo at a time
- **Merge conflicts** — resolved by pulling latest main before each worktree creation + auto-merging between dependent tasks

---

## PR History

| PR | Title | Source | Review |
|----|-------|--------|--------|
| #1 | Add multiply function | Manual test | N/A |
| #2 | Add subtract function | Worker test | N/A |
| #3 | Add divide function | PLAN.yaml task-001 | N/A |
| #6 | Add power function | PLAN.yaml task-004 | N/A |
| #8 | Add Calculator class | PLAN.yaml task-006 | Approved (first review test) |
| #9 | Add string utility functions | PLAN.yaml task-007 | REQUEST_CHANGES → Fixed → Approved |
| #11 | Add statistics module | GitHub Issue #10 | Approved + auto-merged + issue closed |
| #13 | Add conversion utilities | GitHub Issue #12 | Approved + auto-merged + issue closed |

---

## Future Work

### Near Term
- [ ] **Retry logic** for worker failures (configurable max retries)
- [ ] **CI pipeline** — GitHub Actions for lint/test on PRs
- [ ] **Rate limiter** — track subscription usage, switch to API when near limit
- [ ] **Branch protection** — require PR + CI checks on main

### Medium Term
- [ ] **Task decomposition agent** — Claude breaks vague issues into scoped sub-tasks
- [ ] **Conflict resolution agent** — auto-resolve merge conflicts
- [ ] **API key fallback** — overnight batch runs when subscription is exhausted
- [ ] **Multi-repo support** — orchestrator manages multiple repos

### Long Term
- [ ] **Agent Teams integration** — use Claude Code native Agent Teams as workers
- [ ] **Dashboard web UI** — real-time monitoring beyond the terminal
- [ ] **Deploy to our-caring-circle** — production use on family care app
- [ ] **GitHub Actions trigger** — run orchestrator on issue label events

---

## Prerequisites

- [x] Claude Code CLI installed (v2.1.59+)
- [x] GitHub CLI authenticated as Ludicrous09
- [x] Python 3.12 with venv
- [x] Dependencies: pyyaml, rich, gitpython
- [x] Test repo created with agent labels
- [x] Max 5x subscription active
- [ ] Branch protection rules on main
- [ ] GitHub Actions CI workflow
- [ ] Anthropic API key (for future overflow usage)