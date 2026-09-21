"""Typer application object for the ``strutil`` command.

Layering rule: this package imports ``src/string_utils.py`` directly and never
``src/api/``. No HTTP client libraries and no Pydantic. Subcommands are
registered on ``app`` by later modules.
"""

import typer

app = typer.Typer(
    name="strutil",
    help="Command-line interface to the strutil string utilities.",
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    # A callback keeps ``app`` a command group while it has no subcommands
    # registered; Typer refuses to build a command from an empty application.
    pass
