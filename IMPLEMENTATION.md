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
│  • Reads GitHub Issues or PLAN.yaml                      │
│  • Resolves dependencies + file conflict detection       │
│  • Spawns Claude Code workers in isolated worktrees      │
│  • Review → Fix → Re-review → Auto-merge pipeline       │
│  • Retries failed workers (configurable, default 2)      │
│  • Enforces file scope (workers can't modify unlisted)   │
│  • Rich dashboard with timing + cost tracking            │
│  • Dry-run mode to preview execution plan                │
│  • Targets any repo via --repo-path                      │
│  • Task decomposition for vague issues (--decompose)     │
│  • Conflict resolution before marking tasks failed       │
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
agent-shop-testbed/agent-shop/   ← Stable copy (run orchestrator from here)
              ↓ targets ↓
agent-shop/                      ← Self-improving tool (agents modify this)
              ↓ targets ↓
our-caring-circle/               ← Production app (next target)
```

**Sync after improvements:**
```bash
cd ~/code/personal/agent-shop
bash sync.sh  # copies code to testbed
```

**Running:**
```bash
cd ~/code/personal/agent-shop-testbed
source agent-shop/.venv/bin/activate
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/agent-shop \
  --log-dir agent-shop/logs \
  --max-workers 2
```

---

## Module Inventory

| Module | Purpose | Added |
|--------|---------|-------|
| `orchestrator.py` | Main async loop — spawns workers, manages state, dashboard | Phase 2 |
| `worker.py` | Claude Code headless worker with worktree isolation | Phase 1 |
| `reviewer.py` | Code review agent — reads diffs, posts structured reviews | Phase 3 |
| `fixer.py` | Fix agent — addresses review feedback with linked commits | Phase 3 |
| `task_manager.py` | PLAN.yaml parser and dependency resolver | Phase 2 |
| `issue_source.py` | GitHub Issues as task source | Phase 4 |
| `decomposer.py` | Task decomposition — breaks vague issues into sub-tasks | Self-improvement R2 |
| `conflict_resolver.py` | Auto-resolves merge conflicts via Claude | Self-improvement R2 |
| `sync.sh` | Copies agent-shop code to testbed | Self-improvement R2 |

### Tests

| Test File | Covers |
|-----------|--------|
| `tests/test_task_manager.py` | PLAN.yaml parsing, dependency resolution, ready task filtering |
| `tests/test_issue_source.py` | Issue body parsing, file extraction, depends_on, max_turns |
| `tests/test_worker.py` | File scope enforcement |
| `tests/test_orchestrator_paths.py` | --repo-path and --log-dir behavior, decompose integration |
| `tests/test_orchestrator_timing.py` | Duration tracking, cost tracking, summary stats |
| `tests/test_dry_run.py` | Dry-run mode output |
| `tests/test_conflict_resolver.py` | Conflict detection, resolution, error handling |
| `tests/test_decomposer.py` | Task decomposition, sub-issue creation, error handling |
| `test_retry.py` | Retry logic, branch cleanup, suffix handling |

---

## CLI Options

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
| `--dry-run` | `False` | Preview execution plan without running |
| `--decompose` | `False` | Auto-decompose vague issues into sub-tasks |

---

## Build History

### Phase 1-4: Foundation → Full Pipeline (testbed)

Built and proven in agent-shop-testbed:
- Worker with worktree isolation and Claude Code headless mode
- Orchestrator with async parallel execution and dependency resolution
- Review agent that catches real bugs (proven: truncate function negative index)
- Fix agent that addresses review feedback with linked commits
- GitHub Issues as task source with template support
- Auto-merge pipeline: Worker → Review → Fix → Re-review → Merge → Close Issue

### Self-Improvement Round 1 (agent-shop issues #1-4)

Agents improved themselves — orchestrator ran from testbed targeting agent-shop:

| Issue | Title | PR | Status |
|-------|-------|----|--------|
| #1 | Retry logic for worker failures | #7 | ✅ Merged |
| #2 | GitHub Actions CI pipeline | #5 | ✅ Merged |
| #3 | External repo targeting (--repo-path) | #8 | ✅ Merged (conflict resolved) |
| #4 | Unit tests for task_manager and issue_source | #6 | ✅ Merged |

**Bug discovered:** `CLAUDECODE` env var blocks nested Claude sessions. Fixed in worker, reviewer, and fixer.

### Self-Improvement Round 2 (agent-shop issues #9-15)

| Issue | Title | PR | Status | Notes |
|-------|-------|----|--------|-------|
| #9 | File scope enforcement | #17 | ✅ Merged | Workers can't modify unlisted files |
| #10 | Sync script | #19 | ✅ Merged | `bash sync.sh` copies to testbed |
| #11 | Dry-run mode | #21 | ✅ Merged | `--dry-run` previews execution plan |
| #12 | Review agent quality | #16 | ✅ Merged | Fewer false positive REQUEST_CHANGES |
| #13 | Dashboard timing + cost | #18 | ✅ Merged | Duration and cost columns in dashboard |
| #14 | Task decomposition agent | #23 | ✅ Merged | `--decompose` breaks vague issues into sub-tasks |
| #15 | Conflict resolution agent | #22 | ✅ Merged | Auto-resolves merge conflicts via Claude |

**4 completed by orchestrator automatically, 3 failed review/merge (merge conflicts from parallel PRs). All 3 rescued via manual review + rebase + merge.**

---

## Open Issues

| Issue | Title | Priority |
|-------|-------|----------|
| #20 | Post-work rebase step before pushing | 1 |

---

## Next Issues to Create

### High Priority — Needed for our-caring-circle

- **CLAUDE.md generator** — auto-generate a CLAUDE.md for target repos by analyzing the codebase (tech stack, conventions, test framework)
- **Better error messages on review/merge failure** — currently just "Review/fix/merge cycle failed" with no detail. Log the actual exception/stderr to the issue comment
- **PR conflict detection before merge** — check `gh pr view --json mergeable` before attempting `gh pr merge`, run conflict_resolver if not mergeable
- **Priority batching** — complete all priority:1 tasks and merge them before starting priority:2

### Medium Priority — Quality of Life

- **Worker prompt customization** — allow CLAUDE.md or per-task prompt overrides for different repos
- **Cost reporting** — aggregate prompt costs per run and per task in final summary
- **Notification on completion** — desktop notification or webhook when orchestrator finishes
- **Smarter review agent** — pass CLAUDE.md context to reviewer so it understands project conventions

### Lower Priority — Future Vision

- **Web dashboard** — real-time monitoring UI instead of terminal
- **GitHub Actions trigger** — run orchestrator when issues are labeled agent-ready
- **Multi-repo orchestration** — process issues across multiple repos in one run
- **Agent Teams integration** — use Claude Code native Agent Teams as workers
- **Prompt caching** — reuse review prompts for re-reviews to save tokens

---

## Known Issues & Lessons

1. **CLAUDECODE nesting** — Claude Code sets env var blocking nested sessions. All subprocess `claude` calls strip it. Use `CLAUDECODE=` prefix when launching from Claude Code terminal.

2. **Merge conflicts from parallel PRs** — When 2+ workers modify the same file (e.g., orchestrator.py), the second PR will have conflicts. Mitigations: file conflict detection, post-work rebase (issue #20), conflict resolver agent.

3. **Review/merge failure reporting** — The orchestrator logs "Review/fix/merge cycle failed" but doesn't surface the actual error to the issue comment.

4. **PR scope creep** — Workers sometimes modify files beyond what the issue requested. Fixed by file scope enforcement (issue #9).

5. **GitHub merge after rebase** — Sometimes `gh pr merge` fails after a local rebase + force push. Workaround: `gh pr checkout N && git merge origin/main && git push`.

6. **Review is sequential** — Reviews run one at a time even when multiple PRs are ready. This is a bottleneck when many tasks complete simultaneously.

---

## Prerequisites

- [x] Claude Code CLI installed (v2.1.59+)
- [x] GitHub CLI authenticated as Ludicrous09
- [x] Python 3.12 with venv
- [x] Dependencies: pyyaml, rich, gitpython
- [x] agent-shop repo with labels
- [x] agent-shop-testbed repo (stable runner)
- [x] Max 5x subscription active
- [x] GitHub Actions CI
- [x] Unit tests for all core modules
- [x] Retry logic
- [x] External repo targeting
- [x] File scope enforcement
- [x] Dry-run mode
- [x] Dashboard timing and cost tracking
- [x] Task decomposition agent
- [x] Conflict resolution agent
- [x] Sync script
- [x] Review agent quality improvements
- [ ] Post-work rebase (issue #20)
- [ ] Priority batching
- [ ] Better error reporting
- [ ] Branch protection rules