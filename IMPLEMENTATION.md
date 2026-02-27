# Agent Shop — Implementation Plan & Status

**Project:** Autonomous Multi-Agent Development Pipeline
**Repos:**
- [Ludicrous09/agent-shop](https://github.com/Ludicrous09/agent-shop) — the tool (standalone, self-improving)
- [Ludicrous09/agent-shop-testbed](https://github.com/Ludicrous09/agent-shop-testbed) — test playground + stable runner

**Runtime:** WSL Ubuntu on local machine
**Language:** Python 3.12 with venv
**Subscription:** Claude Max 5x ($100/mo)

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    YOU (Jason)                            │
│         Create Issues → Label "agent-ready" → Monitor    │
└──────────────┬───────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────┐
│            ORCHESTRATOR (orchestrator.py)                 │
│                                                          │
│  • Reads GitHub Issues (--source issues) or PLAN.yaml    │
│  • Resolves dependencies + file conflict detection       │
│  • Spawns Claude Code workers in isolated worktrees      │
│  • Runs review/fix/merge pipeline after each PR          │
│  • Auto-merges approved PRs, closes issues               │
│  • Retries failed workers (configurable, default 2)      │
│  • Rich terminal dashboard with live status              │
│  • Targets any repo via --repo-path                      │
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
│  • GitHub Actions CI: ruff + pytest on all PRs           │
│  • Labels: agent-ready, agent-failed, agent-created      │
│  • Issue template for structured task creation           │
│  • Auto-closed issues on successful merge                │
└──────────────────────────────────────────────────────────┘
```

---

## Repo Strategy

```
agent-shop-testbed/agent-shop/   ← Stable copy of orchestrator (run from here)
              ↓ targets ↓
agent-shop/                      ← Self-improving tool (agents modify this)
              ↓ targets ↓
our-caring-circle/               ← Production app (future)
```

**Why two repos?**
- **agent-shop** is the tool itself — agents improve it by working on its own issues
- **agent-shop-testbed** holds a stable copy used to run the orchestrator with the dashboard
- Running the orchestrator from testbed targeting agent-shop prevents agents from breaking the tool mid-run
- Once improvements are merged and stable in agent-shop, sync the code back to testbed

**Running:**
```bash
# From testbed (stable), targeting agent-shop (self-improvement)
cd ~/code/personal/agent-shop-testbed
source agent-shop/.venv/bin/activate
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/agent-shop \
  --log-dir agent-shop/logs \
  --max-workers 2

# From testbed, targeting any other repo
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/our-caring-circle \
  --log-dir agent-shop/logs \
  --max-workers 2
```

---

## Pipeline Flow

```
GitHub Issue (agent-ready) → Worker → PR → Review → Fix (if needed) → Merge → Issue Closed
```

1. **Issue created** with `agent-ready` label (via template or manually)
2. **Orchestrator** fetches open `agent-ready` issues, resolves dependencies
3. **Worker agent** creates isolated git worktree, writes code + tests, commits, pushes, opens PR
4. **Review agent** reads full diff + file contents, posts structured code review
5. **Fix agent** (if changes requested) addresses feedback, commits fixes
6. **Review agent** re-reviews (up to 2 fix cycles)
7. **Auto-merge** squash merges approved PR, pulls latest main
8. **Issue closed** with comment linking to merged PR
9. **Next dependent task** starts from updated main

---

## Phase Status

### Phase 1: Foundation ✅ COMPLETE

Single Claude Code worker completing tasks end-to-end.

**What was built:**
- `worker.py` — Core worker module
  - Git worktree isolation at `/tmp/agent-worktrees/`
  - Claude Code headless mode (`claude -p`) with `--dangerously-skip-permissions`
  - Restricted tool access via `--allowedTools`
  - Auto-commit fallback if Claude skips git commands
  - PR creation via `gh pr create`
  - Automatic worktree cleanup
  - Per-worker log files
  - Strips `CLAUDECODE` env var to allow nested sessions

**Key learnings:**
- `--dangerously-skip-permissions` is required for headless mode
- Action-oriented prompts work better than verbose task descriptions
- Model forced to `sonnet` for workers (5x throughput vs Opus)
- Worker timeout of 600s prevents runaway agents
- `CLAUDECODE` env var must be stripped for subprocess claude calls

### Phase 2: Orchestrator ✅ COMPLETE

Parallel worker orchestration with dependency resolution.

**What was built:**
- `orchestrator.py` — Async main loop
  - `asyncio` + `ThreadPoolExecutor` for parallel worker execution
  - Dependency resolution (tasks specify `depends_on`)
  - File conflict detection (tasks specify `files_touched`)
  - Rich live table showing task status
  - `status.json` written after every state change
  - CLI: `--plan`, `--source`, `--label`, `--max-workers`, `--timeout`, `--repo-path`, `--log-dir`, `--max-retries`
  - Graceful `KeyboardInterrupt` handling
- `task_manager.py` — PLAN.yaml parser with dependency validation

### Phase 3: Review & Fix Pipeline ✅ COMPLETE

Automated code review with feedback loop.

**What was built:**
- `reviewer.py` — Code review agent (reads diffs, posts structured reviews)
- `fixer.py` — Review feedback fixer (addresses comments, pushes fixes)
- Integrated pipeline: Worker → Review → Fix → Re-review → Merge

**Real example (testbed PR #9):** Review agent found a real bug in a truncate function (negative index issue). Fix agent resolved it. Re-review approved. Auto-merged.

### Phase 4: GitHub Issues as Task Source ✅ COMPLETE

**What was built:**
- `issue_source.py` — GitHub Issues parser (supports both freeform and template format)
- `.github/ISSUE_TEMPLATE/agent-task.yml` — Structured issue template
- Labels: `agent-ready`, `agent-failed`, `agent-created`, `priority:1-3`

### Phase 5: Self-Improvement ✅ COMPLETE

The agent-shop repo successfully improved itself by processing its own issues.

**Self-improvement cycle (agent-shop repo):**

| Issue | Title | PR | Result |
|-------|-------|----|--------|
| #1 | Add retry logic for worker failures | #7 | Merged ✅ |
| #2 | Add GitHub Actions CI pipeline | #5 | Merged ✅ |
| #3 | Fix orchestrator --repo-path for external repos | #8 | Merged ✅ (conflict resolved) |
| #4 | Add unit tests for task_manager and issue_source | #6 | Merged ✅ |

**What was added:**
- Retry logic with configurable max retries, branch cleanup between attempts
- GitHub Actions CI with ruff lint + pytest on all PRs
- `--repo-path` and `--log-dir` for targeting external repos
- Unit tests for task_manager and issue_source
- `CLAUDECODE` env var stripping in worker, reviewer, and fixer

**Bug discovered:** Claude Code sets a `CLAUDECODE` env var that prevents nested sessions. All subprocess calls that invoke `claude` must strip this variable from the environment.

---

## File Structure

### agent-shop (standalone tool)

```
agent-shop/
├── orchestrator.py             # Main loop — spawns workers, manages state
├── worker.py                   # Claude Code headless worker
├── reviewer.py                 # Code review agent
├── fixer.py                    # Review feedback fixer
├── task_manager.py             # PLAN.yaml parser + dependency resolver
├── issue_source.py             # GitHub Issues as task source
├── CLAUDE.md                   # Context for Claude Code sessions
├── status.json                 # Live orchestration state
├── logs/                       # Per-worker execution logs
├── tests/                      # Unit tests
│   ├── test_task_manager.py
│   ├── test_issue_source.py
│   └── test_orchestrator_paths.py
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   └── agent-task.yml
│   └── workflows/
│       └── ci.yml              # Ruff + pytest on PRs
├── requirements-dev.txt        # pytest, ruff, pytest-mock
└── .venv/                      # Python virtual environment
```

### agent-shop-testbed (test playground + stable runner)

```
agent-shop-testbed/
├── agent-shop/                 # Stable copy of orchestrator (synced from agent-shop)
│   ├── orchestrator.py
│   ├── worker.py
│   ├── reviewer.py
│   ├── fixer.py
│   ├── task_manager.py
│   ├── issue_source.py
│   ├── .venv/
│   └── logs/
├── src/                        # Test application code (built by agents)
├── tests/                      # Test suite (built by agents)
├── PLAN.yaml                   # Task definitions
├── .github/ISSUE_TEMPLATE/
│   └── agent-task.yml
└── IMPLEMENTATION.md           # This document
```

---

## Usage

### Self-Improvement (agent-shop works on itself)

```bash
cd ~/code/personal/agent-shop-testbed
source agent-shop/.venv/bin/activate
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/agent-shop \
  --log-dir agent-shop/logs \
  --max-workers 2 --timeout 600
```

### Target Any Repo

```bash
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/our-caring-circle \
  --log-dir agent-shop/logs \
  --max-workers 2
```

### From PLAN.yaml

```bash
CLAUDECODE= python agent-shop/orchestrator.py --plan PLAN.yaml --repo-path ~/code/my-repo
```

### CLI Options

| Flag | Default | Description |
|------|---------|-------------|
| `--source` | `plan` | Task source: `plan` or `issues` |
| `--plan` | `PLAN.yaml` | Path to plan file |
| `--label` | `agent-ready` | GitHub label filter for issues |
| `--repo-path` | `.` | Path to target git repo |
| `--max-workers` | `2` | Maximum parallel worker agents |
| `--max-retries` | `2` | Retry attempts for failed workers |
| `--timeout` | `600` | Per-task timeout (seconds) |
| `--log-dir` | `./logs` | Directory for worker logs |

### Run Individual Components

```bash
CLAUDECODE= python reviewer.py --pr 13
CLAUDECODE= python fixer.py --pr 13
python issue_source.py
```

---

## Issue Format

Use the built-in template (🤖 Agent Task) or write manually:

```markdown
### Description

Add a `foo(x: int) -> str` function to src/bar.py that...

### Files

- src/bar.py
- tests/test_bar.py

### Depends on

#5, #6

### Max turns

40
```

Priority set via labels: `priority:1` (high), `priority:2` (medium), `priority:3` (low).

---

## Cost Management

**Primary: Max 5x Subscription ($100/mo)** — all agents included, no extra cost. Workers default to `sonnet` model.

**Note:** The `CLAUDECODE=` prefix is required when launching from a Claude Code session. In a plain terminal it's harmless.

---

## Complete PR History

### agent-shop-testbed (test playground)

| PR | Title | Source | Review | Status |
|----|-------|--------|--------|--------|
| #1 | Add multiply function | Manual test | N/A | Merged |
| #2 | Add subtract function | Worker test | N/A | Merged |
| #3 | Add divide function | PLAN.yaml | N/A | Merged |
| #6 | Add power function | PLAN.yaml | N/A | Merged |
| #8 | Add Calculator class | PLAN.yaml | Approved | Merged |
| #9 | Add string utilities | PLAN.yaml | REQUEST_CHANGES → Fixed → Approved | Merged |
| #11 | Add statistics module | Issue #10 | Approved + auto-merged | Merged |
| #13 | Add conversion utilities | Issue #12 | Approved + auto-merged | Merged |

### agent-shop (self-improvement)

| PR | Title | Issue | Review | Status |
|----|-------|-------|--------|--------|
| #5 | Add GitHub Actions CI pipeline | #2 | N/A (manual) | Merged |
| #6 | Add unit tests for task_manager and issue_source | #4 | Approved (4 suggestions) | Merged |
| #7 | Add retry logic for worker failures | #1 | REQUEST_CHANGES (3 comments) | Merged |
| #8 | Fix orchestrator --repo-path for external repos | #3 | N/A (conflict resolved manually) | Merged |

---

## Known Issues & Lessons Learned

- **CLAUDECODE nesting** — Claude Code sets env var blocking nested sessions. All subprocess `claude` calls strip it.
- **Review comments are PR-level** — GitHub API doesn't allow self-approval, so reviews are posted as comments with `[REVIEW: APPROVE/REQUEST_CHANGES]` tags.
- **Merge conflicts on parallel PRs** — When two PRs modify the same file, the second needs manual rebase. Orchestrator mitigates this by merging sequentially and pulling main between tasks.
- **PR #5 scope creep** — CI pipeline issue caused the agent to also ruff-fix all Python files. Issue descriptions should specify "ONLY modify the listed files."

---

## Future Work

### Near Term
- [ ] **Rate limiter** — track subscription usage, switch to API when near limit
- [ ] **Branch protection** — require PR + CI checks on main
- [ ] **Sync script** — automate copying agent-shop code to testbed
- [ ] **Stricter file scope** — enforce "only touch listed files" in worker prompt

### Medium Term
- [ ] **Task decomposition agent** — Claude breaks vague issues into scoped sub-tasks
- [ ] **Conflict resolution agent** — auto-resolve merge conflicts via Claude
- [ ] **API key fallback** — overnight batch runs when subscription is exhausted

### Long Term
- [ ] **Deploy to our-caring-circle** — production use on family care app
- [ ] **Agent Teams integration** — use Claude Code native Agent Teams as workers
- [ ] **Dashboard web UI** — real-time monitoring beyond the terminal
- [ ] **GitHub Actions trigger** — run orchestrator on issue label events

---

## Prerequisites

- [x] Claude Code CLI installed (v2.1.59+)
- [x] GitHub CLI authenticated as Ludicrous09
- [x] Python 3.12 with venv
- [x] Dependencies: pyyaml, rich, gitpython
- [x] agent-shop repo with labels
- [x] agent-shop-testbed repo with labels
- [x] Max 5x subscription active
- [x] GitHub Actions CI on agent-shop
- [x] Unit tests for core modules
- [x] Retry logic implemented
- [x] External repo targeting (--repo-path)
- [ ] Branch protection rules on main
- [ ] Anthropic API key (for future overflow usage)