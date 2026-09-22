"""Single rendering module for all ``strutil`` CLI output.

Every subcommand must route its output through :func:`render` rather than
calling ``print`` directly, so the machine-readable JSON contract stays
identical across subcommands and is auditable in one place. Diagnostics,
prompts, and anything not part of the command's result belong on stderr via
:func:`diagnostic`, never on stdout.

This module intentionally does not import the API's Pydantic response
models: the CLI and API wire formats are versioned independently, and
reusing the API models would mean an HTTP response shape change could
silently break every script parsing CLI output.
"""

import json
import sys
from typing import Any

EXIT_SUCCESS = 0
"""The command completed successfully."""

EXIT_FAILURE = 1
"""The command ran but failed for an expected, operational reason."""

EXIT_USAGE = 2
"""The command could not run because of a usage or configuration error."""


def render(
    command: str,
    ok: bool,
    result: Any = None,
    error: str | None = None,
    format: str = "text",
) -> None:
    """Write a command's outcome to stdout in the requested format.

    In ``"json"`` format this writes exactly one JSON object -- with the
    top-level keys ``command``, ``ok``, ``result``, ``error`` and nothing
    else -- followed by a single trailing newline. In ``"text"`` format it
    writes a human-readable line instead.
    """
    if format == "json":
        payload = {"command": command, "ok": ok, "result": result, "error": error}
        sys.stdout.write(json.dumps(payload, default=str) + "\n")
        return

    if ok:
        sys.stdout.write(f"{result}\n" if result is not None else "")
    else:
        sys.stdout.write(f"{command}: {error or 'unknown error'}\n")


def diagnostic(message: str) -> None:
    """Write a diagnostic message to stderr. Never writes to stdout."""
    sys.stderr.write(f"{message}\n")
