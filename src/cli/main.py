"""Typer application object for the ``strutil`` command.

Layering rule: this package imports ``src/string_utils.py`` directly and never
``src/api/``. No HTTP client libraries and no Pydantic. Subcommands are
registered on ``app`` by later modules.
"""

import enum
from typing import Annotated

import typer

from src.cli.config import ConfigError, load_config
from src.cli.render import EXIT_FAILURE, EXIT_USAGE, diagnostic

app = typer.Typer(
    name="strutil",
    help="Command-line interface to the strutil string utilities.",
    no_args_is_help=True,
)


class _Simulate(enum.StrEnum):
    """Values for the hidden ``--_simulate`` flag, used only by exit-code tests.

    TODO: remove ``--_simulate`` and this enum once a real subcommand exists
    to exercise the operational-failure exit path; until then it is reachable
    (though undocumented) by any caller, not just the test suite.
    """

    none = "none"
    operational_error = "operational-error"


@app.callback(invoke_without_command=True)
def _root(
    simulate: Annotated[
        _Simulate,
        typer.Option(
            "--_simulate",
            hidden=True,
            help="Internal only: exercise an exit-code path before real subcommands exist.",
        ),
    ] = _Simulate.none,
) -> None:
    # ``invoke_without_command`` lets a bare, non-empty invocation (e.g. a
    # flag with no subcommand) run this body -- such as surfacing a malformed
    # config file -- instead of Click's default "Missing command" error. A
    # genuinely empty invocation is still handled by ``no_args_is_help``
    # before this callback ever runs.
    try:
        load_config()
    except ConfigError as exc:
        diagnostic(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    if simulate is _Simulate.operational_error:
        try:
            raise ValueError("simulated operational failure")
        except ValueError as exc:
            diagnostic(str(exc))
            raise typer.Exit(EXIT_FAILURE) from exc


from . import commands  # noqa: E402,F401 registers subcommands on ``app``
