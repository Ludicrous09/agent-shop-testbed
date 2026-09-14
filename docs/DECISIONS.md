---
approved: true
---

# Decision record

> **Approved 2026-09-13**, after two amendments recorded inline below: the
> `src/strutil/` restructure was cut down to a new sibling package, and the
> `pyproject.toml` reversal of the previous record's explicit rejection was
> written down rather than left implicit.

## Direction

a command-line interface for the string utilities, with subcommands, a config file for defaults, and machine-readable output for scripting

## Decisions

### language and framework

**Python 3.11+, with the CLI built on Typer (Click-based) as a `strutil` command exposing one subcommand per public string utility. The CLI imports the existing string-utilities core directly and adds no new runtime dependency beyond Typer.**

The repository is already Python with a FastAPI/Pydantic HTTP layer over the same core, so the CLI must be a sibling adapter in the same language rather than a separate program. Typer gives subcommands, typed options, generated `--help`, and correct exit-code plumbing without hand-rolled parsing, and its Click foundation is the most widely understood CLI idiom in Python.

Rejected:
- **stdlib `argparse`** — Zero new dependencies is a weak argument in a repo that already ships FastAPI and Pydantic. Subparser wiring, typed coercion, and per-subcommand help would all be hand-written and would drift as utilities are added.
- **Click used directly** — Typer is Click underneath, so this buys the same engine while giving up type-hint-driven signatures that keep subcommands in step with the core function signatures.
- **A separate CLI in Go or Rust for a fast single binary** — Would fork the string-utility logic into a second implementation, guaranteeing divergence from the API. Startup time is not a constraint for this tool.
- **Exposing the CLI as a `python src/cli.py` script with manual `sys.argv` handling** — No subcommand structure, no help text, and no installable entry point — it fails the stated direction immediately.

### deployment

**Ship as an installable package: add `pyproject.toml` declaring a `strutil` console-script entry point, installed with `pip install -e .` for development and `pip install .`/pipx for use. `python -m strutil` is supported as an exactly equivalent invocation. `requirements.txt` remains the pinned set CI installs. Nothing is published to PyPI and no image is built.**

A console-script entry point is what makes subcommands and config-file discovery behave like a real tool rather than a script run from the repo root. Keeping `requirements.txt` as CI's input avoids disturbing the workflow that already gates this repository.

Rejected:
- **Leave the repo unpackaged and document `python src/cli/main.py ...`** — Breaks when run from any directory other than the repo root, makes `sys.path` the user's problem, and gives scripts no stable command name to call.
- **Distribute a Docker image as the primary interface** — A container boundary defeats the point of a scripting tool: piping stdin/stdout, reading a config file from the working directory, and relative paths all become friction.
- **Freeze a single binary with PyInstaller or Nuitka** — Adds a build toolchain and a platform matrix to solve a distribution problem this project does not yet have.
- **Publish to PyPI as part of this work** — Claims a public name and creates a release obligation before the command surface has stabilized.
- **Serve the CLI by shelling out to the running FastAPI server** — Makes a local string transformation require a live HTTP service; see the module-boundaries decision.

### packaging, and a rejection this record reverses

**This record adds `pyproject.toml` with a `strutil` console-script entry
point. The previous decision record explicitly rejected exactly that**, on the
grounds that "changing packaging is unrelated churn that would collide with
every other agent's branch".

The reversal is deliberate and narrow. That rejection was right for the API
milestone, where packaging bought nothing: the service ran via `uvicorn
src.api.app:app` and needed no entry point. A CLI whose whole deliverable is an
installed `strutil` command cannot be delivered without one. The collision risk
the old record named is real and is why `pyproject.toml` must be created by a
single issue that nothing else runs alongside.

Recorded here rather than left to git history, because `intake --force`
overwrote the file that held the original rejection, and a decision that
supersedes another has to say so.

### storage

**The CLI is stateless — it persists nothing and caches nothing. The only file it reads is a TOML config supplying flag defaults, parsed with stdlib `tomllib`, discovered as `./strutil.toml` first, then `$XDG_CONFIG_HOME/strutil/config.toml` (falling back to `~/.config/strutil/config.toml`). Resolution order is: explicit command-line flag > `STRUTIL_*` environment variable > config file > built-in default. A missing config file is not an error; a malformed one is a usage error.**

String utilities are pure functions, so the only state worth keeping is the user's preferred defaults. TOML is parseable by the standard library on the chosen Python version, which keeps the config format from adding a dependency.

Rejected:
- **YAML config** — Requires PyYAML purely for a defaults file, and brings type-coercion surprises that TOML does not have.
- **JSON config** — No comments, and no way for a user to annotate why a default is set — poor ergonomics for a hand-edited file.
- **INI via `configparser`** — Everything is a string, so booleans and numbers need per-key coercion, and nested sections are awkward.
- **A SQLite database or an on-disk result cache** — There is no state to store and no computation expensive enough to cache; it would add invalidation bugs for no gain.
- **Home-directory config only (no project-local file)** — Prevents a repository from checking in its own defaults, which is the case that matters most for scripting.
- **Environment variables as the only configuration mechanism** — The direction explicitly calls for a config file, and env-only defaults are invisible and hard to review.

### module boundaries

**The existing layout stays put. `src/string_utils.py` is not moved and `src/api/` is not moved; a new `src/cli/` is added alongside them, holding Typer commands, config loading, and output rendering. `src/cli/` and `src/api/` each import `src/string_utils.py` directly and never each other. The CLI must not make HTTP calls and must not import Pydantic request/response models. Output rendering lives in a single `cli/render.py`: human-readable text by default, and under `--format json` a single JSON object on stdout with stable keys (`command`, `ok`, `result`, `error`), with all diagnostics and prompts on stderr. Exit codes: 0 success, 1 expected operational failure, 2 usage or config error.**

The API already proved the core is reusable behind an adapter; the CLI is the second adapter, and keeping both thin preserves one implementation of every utility.

**Amended before approval.** The proposal was to restructure everything into
`src/strutil/{core,cli,api}/`. Rejected: that moves every existing file, so it
collides with any concurrent branch and rewrites the API work delivered in the
previous milestone, for no benefit the CLI actually needs. A new sibling
package gets the same layering with a diff nobody else touches. Confining formatting to one module is what makes the machine-readable contract auditable rather than scattered across subcommands.

Rejected:
- **CLI implemented as a client of the local HTTP API** — Requires a running server for offline string work, adds network failure modes to a pure function call, and makes the CLI's behavior depend on deployment state.
- **Reusing the API's Pydantic response models as the CLI's JSON output contract** — Couples two independently versioned wire formats — an HTTP response shape change would silently break every script parsing CLI output.
- **Putting Typer decorators directly on the core string functions** — Drags CLI concerns into the layer the API also consumes and makes the core untestable without invoking a command runner.
- **A shared `services/` layer between core and both adapters** — There is no orchestration for it to hold; it would become a pass-through that duplicates core signatures.
- **Per-subcommand ad-hoc printing with `print()`** — Guarantees drift in the JSON shape between subcommands and makes the stdout/stderr split impossible to enforce.

### quality gates

**Extend the existing `verify` script and `ci.yml` rather than adding a new workflow. Gates: (1) pytest exercises every subcommand through Typer's `CliRunner`, asserting stdout, stderr, and exit code; (2) for every subcommand, `--format json` output is parsed and validated against the documented key set, and stdout is asserted to contain nothing but that JSON; (3) a parity test asserts every public string utility has both an API route and a CLI subcommand — the CLI counterpart of the OpenAPI coverage assertion already in place; (4) the existing lint/format checks extend to the new package. CI is green only when `verify` passes end to end.**

This repository already settled on `verify` as the single source of truth that CI mirrors, so the CLI's gates belong inside it. The parity test is the gate that actually matters over time: it is what stops a new utility from reaching one adapter and not the other.

Rejected:
- **Manual testing plus documentation of the commands** — Exit codes and the exact stdout byte stream are the CLI's public contract; nothing but an assertion keeps them stable.
- **Snapshot-testing the human-readable help and output text as the primary gate** — Brittle against wording and terminal-width changes, and it would make cosmetic edits look like regressions while missing JSON contract breaks.
- **A separate GitHub Actions workflow for CLI tests** — Splits the enforced gate in two and re-opens the drift between local `verify` and CI that the existing chore commit closed.
- **A mandated 100% line-coverage threshold** — Pushes effort toward argument-plumbing coverage instead of the contract assertions that catch real breakage.
- **Introducing a new type-checker or linter stack alongside what CI already enforces** — Changes the quality bar for the whole repository under cover of a CLI feature; the new package meets the existing bar instead.

## Milestone

**`strutil` command installed, with every string utility as a subcommand, TOML defaults, and JSON output**

DONE WHEN After `pip install -e .` from a clean checkout: `strutil --help` and `python -m strutil --help` both list one subcommand per public string utility, and the parity test confirms that list matches the routes in the generated OpenAPI document with no gaps in either direction. Each subcommand run with `--format json` writes exactly one JSON object to stdout containing `command`, `ok`, and `result`, with stdout parseable by `json.loads` with no other bytes present, and all diagnostics on stderr. A `strutil.toml` in the working directory that sets a default for a flag changes the result when that flag is omitted, and that value is overridden when the flag is passed explicitly. A successful run exits 0; an unknown subcommand, a bad flag value, or a malformed config file exits 2 with the message on stderr and nothing on stdout. `verify` passes locally and the same run is green in `ci.yml`.
