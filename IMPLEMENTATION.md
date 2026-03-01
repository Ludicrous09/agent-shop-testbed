# Agent Shop — Implementation Plan & Status

**Project:** Autonomous Multi-Agent Development Pipeline
**Repos:**
- [Ludicrous09/agent-shop](https://github.com/Ludicrous09/agent-shop) — the tool (standalone, self-improving)
- [Ludicrous09/agent-shop-testbed](https://github.com/Ludicrous09/agent-shop-testbed) — test playground + stable runner

**Runtime:** WSL Ubuntu on local machine
**Language:** Python 3.12 with venv
**Subscription:** Claude Max 5x ($100/mo)
**Stats:** 38 merged PRs, 4 self-improvement rounds

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
│  • Priority batching (completes P1 before starting P2)   │
│  • --max-priority to limit scope                         │
│  • Dependency resolution + file conflict detection       │
│  • Spawns Claude Code workers in isolated worktrees      │
│  • Post-work rebase before push (prevents conflicts)     │
│  • Review → Fix → Re-review → Auto-merge pipeline       │
│  • Conflict resolution on merge failures                 │
│  • Retries failed workers (configurable, default 2)      │
│  • Enforces file scope (workers can't modify unlisted)   │
│  • Rich dashboard with timing + cost tracking            │
│  • Dry-run mode to preview execution plan                │
│  • Auto-creates follow-up issues from review feedback    │
│  • Targets any repo via --repo-path                      │
│  • Task decomposition for vague issues (--decompose)     │
│  • Architect agent enrichment (--architect)               │
│  • CLAUDE.md generator for target repos                  │
│  • Desktop notifications on completion                   │
│  • Detailed error reporting on failures                  │
│  • Skips issues with already-merged PRs                  │
└──────┬──────────────┬──────────────┬─────────────────────┘
       │              │              │
       ▼              ▼              ▼
┌────────────┐ ┌────────────┐ ┌──────────────┐
│  Worker    │ │  Worker    │ │ Review Agent │
│  Agent     │ │  Agent     │ │              │
│ (headless) │ │ (headless) │ │ Reviews PR   │
│ worktree/A │ │ worktree/B │ │ Posts verdict│
│ Code+Test  │ │ Code+Test  │ │ Fix→Re-review│
│ Rebase+PR  │ │ Rebase+PR  │ │ Auto-merge   │
└─────┬──────┘ └─────┬──────┘ └──────┬───────┘
      │              │               │
      ▼              ▼               ▼
┌──────────────────────────────────────────────────────────┐
│                    GitHub                                 │
│  • Feature branches with PRs                             │
│  • GitHub Actions CI: ruff + pytest on all PRs           │
│  • Labels: agent-ready, agent-failed, agent-created      │
│  • Labels: priority:1/2/3, review-followup               │
│  • Issue template for structured task creation           │
│  • Auto-closed issues on successful merge                │
│  • Auto-created follow-up issues from review feedback    │
└──────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Aliases (configured in ~/.bashrc)

```bash
agent-run       # Run priority 1-2 issues (safe default)
agent-run-all   # Run all priorities including P3 cleanup
agent-dry       # Preview execution plan without running
agent-sync      # Sync agent-shop code to testbed + commit + push
```

### Manual Run

```bash
cd ~/code/personal/agent-shop-testbed
source agent-shop/.venv/bin/activate

# Self-improvement (agents modify agent-shop)
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/agent-shop \
  --log-dir agent-shop/logs \
  --max-workers 2 \
  --max-priority 2 \
  --timeout 600

# Target any repo
CLAUDECODE= python agent-shop/orchestrator.py \
  --source issues \
  --repo-path ~/code/personal/our-caring-circle \
  --log-dir agent-shop/logs \
  --max-workers 2
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

**Workflow:**
1. Create issues on target repo with `agent-ready` label
2. `agent-sync` to copy latest code to testbed
3. `agent-run` to process issues
4. Review results, merge any failed PRs manually if needed
5. `agent-sync` again after self-improvement rounds

---

## Module Inventory

| Module | Purpose | Trigger |
|--------|---------|---------|
| `orchestrator.py` | Main async loop — spawns workers, manages state, dashboard | Always |
| `worker.py` | Claude Code headless worker with worktree isolation | Always — every task |
| `reviewer.py` | Code review agent — reads diffs, posts structured reviews | Always — every PR |
| `fixer.py` | Fix agent — addresses review feedback | When review says REQUEST_CHANGES |
| `conflict_resolver.py` | Auto-resolves merge conflicts via Claude | Auto — when merge fails |
| `task_manager.py` | PLAN.yaml parser and dependency resolver | When `--source plan` |
| `issue_source.py` | GitHub Issues as task source | When `--source issues` |
| `decomposer.py` | Breaks vague issues into scoped sub-tasks | When `--decompose` flag |
| `architect.py` | Designs solutions with Opus/CLI before workers execute | When `--architect` flag |
| `claude_md_generator.py` | Auto-generates CLAUDE.md for target repos | When `--generate-claude-md` flag |
| `sync.sh` | Copies all .py files to testbed | Manual: `agent-sync` |

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
| `--max-priority` | `None` | Stop after this priority level (e.g., 2 = skip P3) |
| `--timeout` | `600` | Per-task timeout (seconds) |
| `--log-dir` | `./logs` | Directory for worker logs |
| `--dry-run` | `False` | Preview execution plan without running |
| `--decompose` | `False` | Auto-decompose vague issues into sub-tasks |
| `--architect` | `False` | Enrich tasks with Opus spec before workers execute |
| `--generate-claude-md` | `False` | Auto-generate CLAUDE.md for target repo |

---

## Self-Improvement History

### Round 1 — Foundation (issues #1-4)

| Issue | Title | PR | Status |
|-------|-------|----|--------|
| #1 | Retry logic for worker failures | #7 | ✅ |
| #2 | GitHub Actions CI pipeline | #5 | ✅ |
| #3 | External repo targeting | #8 | ✅ |
| #4 | Unit tests for core modules | #6 | ✅ |

### Round 2 — Features (issues #9-15)

| Issue | Title | PR | Status |
|-------|-------|----|--------|
| #9 | File scope enforcement | #17 | ✅ |
| #10 | Sync script | #19 | ✅ |
| #11 | Dry-run mode | #21 | ✅ |
| #12 | Review agent quality | #16 | ✅ |
| #13 | Dashboard timing + cost | #18 | ✅ |
| #14 | Task decomposition agent | #23 | ✅ |
| #15 | Conflict resolution agent | #22 | ✅ |

### Round 3 — Robustness (issues #20-42)

| Issue | Title | PR | Status |
|-------|-------|----|--------|
| #20 | Post-work rebase before push | #33 | ✅ |
| #24 | Detailed error messages | #32 | ✅ |
| #25 | PR mergeability check | #31 | ✅ |
| #26 | Priority batching | #30 | ✅ |
| #27 | Architect agent | #35 | ✅ |
| #28 | Auto-create follow-up issues | #34 | ✅ |
| #29 | Update sync.sh | — | ✅ (manual) |
| #36 | Path traversal security fixes | #45 | ✅ |
| #37 | ARG_MAX temp file fix | #47 | ✅ |
| #38 | Subprocess timeout/returncode | #52 | ✅ |
| #39 | Missing unit tests | #50 | ✅ |
| #40 | Mergeability UNKNOWN retry | #55 | ✅ |
| #41 | Cleanup: type hints, dead code | #58 | ✅ |
| #42 | Skip already-merged issues | #44 | ✅ |

### Round 4 — Polish (issues #43-92)

| Issue | Title | PR | Status |
|-------|-------|----|--------|
| #43 | Architect to CLI (remove API dep) | merged | ✅ |
| #61 | --max-priority flag | merged | ✅ |
| #62 | CLAUDE.md generator | merged | ✅ |
| #63 | CLAUDE.md in worker prompts | merged | ✅ |
| #64 | Desktop notifications | merged | ✅ |
| #46-59 | Review follow-up cleanup (9 items) | merged | ✅ |
| #60 | Better follow-up issue quality | — | ❌ (in progress) |
| #66-92 | Review follow-up items (round 4) | — | Open (P3) |

---

## Known Issues & Lessons

1. **CLAUDECODE nesting** — All subprocess `claude` calls strip the CLAUDECODE env var. Use `CLAUDECODE=` prefix when launching from Claude Code terminal.

2. **Merge conflicts** — Mitigated by: post-work rebase, mergeability check + conflict resolver, priority batching, `--max-workers 1` for safety.

3. **Review follow-up quality** — Auto-created issues have truncated titles. Issue #60 addresses this.

4. **CI was broken** — Workflow wasn't installing dependencies. Fixed manually (requirements.txt + updated ci.yml).

5. **GitHub merge after rebase** — Sometimes `gh pr merge` fails after force push. Workaround: `gh pr checkout N && git merge origin/main && git push`.

6. **Review is sequential** — Reviews run one at a time. Bottleneck when many tasks complete simultaneously.

7. **Follow-up breeding** — Each run generates more review follow-ups. Use `--max-priority 2` to prevent chasing cleanup indefinitely.

---

## Future Work

### Ready for our-caring-circle
- [x] CLAUDE.md generator for unknown repos
- [x] CLAUDE.md included in worker prompts
- [x] --max-priority to control scope
- [ ] Test run on our-caring-circle with small issues

### Remaining
- [ ] Fix issue #60 — better follow-up issue titles
- [ ] CI fix for issue template (requirements.txt created)
- [ ] Rate limiter — track subscription usage
- [ ] Web dashboard — real-time monitoring UI
- [ ] GitHub Actions trigger — auto-run on issue label
- [ ] Multi-repo orchestration
- [ ] Branch protection rules on main

---

## Prerequisites

- [x] Claude Code CLI installed
- [x] GitHub CLI authenticated as Ludicrous09
- [x] Python 3.12 with venv
- [x] All runtime dependencies (pyyaml, rich, gitpython, anthropic)
- [x] agent-shop repo with full label set
- [x] agent-shop-testbed as stable runner
- [x] Max 5x subscription active
- [x] GitHub Actions CI (with dependencies)
- [x] Unit tests for all modules
- [x] Retry logic, file scope, priority batching
- [x] Conflict resolution, post-work rebase
- [x] Architect agent, decomposer agent
- [x] CLAUDE.md generator
- [x] Desktop notifications
- [x] Detailed error reporting
- [x] Shell aliases configured
