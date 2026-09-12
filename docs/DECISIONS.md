---
approved: true
---

# Decision record

> **Not approved.** Nothing downstream runs until a human changes
> `approved` to `true` above. That is an explicit act on purpose: a
> record nobody read is not a record anybody agreed to.

## Direction

a small REST API exposing the existing string utilities, so other tools can call them over HTTP

## Decisions

### language and framework

**Python 3.11+ with FastAPI served by uvicorn, with Pydantic models for request and response bodies. Dependencies stay in the existing requirements.txt; no build-system migration.**

The utilities are already Python, so an in-process HTTP wrapper avoids any cross-language boundary, and FastAPI's generated OpenAPI schema is what makes the endpoints discoverable to the "other tools" that are the whole point of the direction.

Rejected:
- **Flask** — No built-in request validation or OpenAPI output; we would hand-roll both, and callers would have no machine-readable contract.
- **Django REST Framework** — Brings an ORM, settings module, and app layout for a service that has no models and no database.
- **Standard-library http.server** — Zero dependency cost, but routing, JSON error shapes, and validation all become bespoke code we then have to test.
- **gRPC service** — The direction says HTTP so other tools can call it; gRPC forces every caller to build stubs.
- **Rewriting the utilities in Go or Node for a faster server** — Discards working, tested Python and doubles the number of implementations to keep correct.
- **Migrating to pyproject.toml/Poetry as part of this work** — The repo uses requirements.txt today; changing packaging is unrelated churn that would collide with every other agent's branch.

### deployment

**A single stateless uvicorn process, containerized with a Dockerfile (python:3.11-slim, non-root user, CMD uvicorn src.api.app:app --host 0.0.0.0 --port 8000). Local development runs the identical command without Docker. No reverse proxy, no process manager, no orchestration manifests.**

The service holds no state and does pure CPU-light string work, so one horizontally-replaceable process is the entire operational story. A container makes that one artifact runnable anywhere without documenting a Python setup.

Rejected:
- **Serverless functions (AWS Lambda, Cloud Run functions)** — Per-function packaging and a vendor-specific handler signature for an API whose value is being trivially runnable by any caller, including on a laptop.
- **Kubernetes manifests or Helm chart** — Orchestration config would outweigh the service it orchestrates; one container needs no scheduler.
- **gunicorn with uvicorn workers behind nginx** — Multi-worker plus a proxy is a scaling answer to a load problem we do not have, and it adds two more components to configure and debug.
- **Heroku/Railway/Fly.io PaaS config** — Ties the repo to one vendor's build system; a plain container leaves that choice to whoever deploys.
- **systemd unit on a long-lived VM** — Pins the service to a hand-maintained host and makes the Python environment part of the deployment surface.

### storage

**None. The service is stateless: every endpoint is a pure function of its request body, nothing is persisted between requests, and no database, cache, queue, or on-disk file is added.**

String utilities have no state to keep, so any store would be infrastructure that can fail, drift, or need migration in exchange for nothing. Statelessness is also what makes the deployment decision above hold.

Rejected:
- **SQLite file for request history** — Turns a replaceable process into one with a disk volume and backup story, to store data no requirement asks for.
- **Redis response cache** — These operations are microseconds of CPU; a network round-trip to a cache would usually be slower than recomputing, and it adds a service that can be down.
- **Postgres for API keys / usage metering** — Presumes an auth and billing model nobody specified; add it when a real requirement arrives.
- **Writing a request/response log file inside the container** — Invisible, unrotated, and lost on restart. Logs go to stdout where the container runtime can collect them.

### module boundaries

**src/string_utils.py (and its sibling utility modules) remain pure, HTTP-unaware functions and are not edited to serve the API. A new src/api/ package holds app.py (FastAPI app and error handlers), routes/strings.py (one endpoint per public utility function), and schemas.py (Pydantic request/response models). Import direction is one-way: src/api/ imports src/string_utils.py, never the reverse. Endpoints are explicit routes such as POST /strings/title-case, each with a named schema; behaviour changes belong in the utility module, and route modules contain no string logic.**

The utilities already have callers and tests that must keep working, so the HTTP layer is additive and the dependency arrow points only one way. Explicit per-function routes are what produce a useful OpenAPI document and let each operation have its own validated input shape.

Rejected:
- **Adding route decorators directly to functions in src/string_utils.py** — Makes FastAPI a hard dependency of code that is imported by non-HTTP callers and tests, and couples every future signature change to the wire format.
- **A single generic POST /call endpoint dispatching by function name via getattr** — Produces an empty OpenAPI contract, gives every operation the same untyped payload, and exposes whatever else lives in the module namespace.
- **One flat api.py containing app, routes, and models** — Fine at three endpoints, unreadable at fifteen, and it invites business logic to settle in the route handler.
- **A service/ layer between routes and utilities** — The utility functions already are the service layer; a pass-through indirection adds a file to edit for every change and hides nothing.
- **Exposing stats.py and conversions.py in the same first pass** — The direction names the string utilities; widening scope now means the boundary gets set by three modules' worth of guesses instead of one working example.

### quality gates

**Ruff for lint and formatting (replacing any ad-hoc style), pytest for tests, and a GitHub Actions workflow at .github/workflows/ci.yml running ruff check, ruff format --check, and pytest on every push and pull request, with a failing job blocking merge. API endpoints are tested through fastapi.testclient.TestClient in tests/api/, and existing tests/ for the utility modules keep passing unchanged. A pull request must not reduce the set of passing tests.**

The repo has no CI today, so the cheapest real gain is making the existing pytest suite actually run on every change rather than adding more tools. Ruff covers lint and format in one fast dependency, and TestClient exercises routing, validation, and serialization without binding a port.

Rejected:
- **black + flake8 + isort** — Three tools, three configs, and three chances to disagree, for what ruff does in one pass.
- **mypy --strict across the repo** — Would force a typing retrofit of existing untyped utility modules before a single endpoint ships; Pydantic already validates everything crossing the HTTP boundary.
- **A coverage percentage threshold (e.g. fail under 90%)** — On a codebase this small the number is trivially gamed by testing easy paths, and it turns into a merge obstacle unrelated to whether the API is correct.
- **pre-commit hooks as the only gate** — Locally skippable and silently absent for any agent or contributor who never installs them; CI is the check that actually holds.
- **Contract tests against a running container in CI** — Needs a build and a health-wait for coverage that TestClient already gives in-process, in a fraction of the time.

## Milestone

**String utilities reachable over HTTP**

DONE WHEN Starting the app with `uvicorn src.api.app:app` and issuing an HTTP request to each public function in src/string_utils.py returns that function's result as JSON with status 200 — matching what calling the function directly in Python returns for the same input; a request with a missing or wrong-typed field returns 422 with a body naming the offending field; an unknown path returns 404 as JSON rather than an HTML page or a stack trace; GET /openapi.json lists every one of those endpoints with a named request and response schema; and `ruff check`, `ruff format --check`, and `pytest` all exit 0 in GitHub Actions on the pull request, with the pre-existing tests/ suite still passing and src/string_utils.py unmodified.
