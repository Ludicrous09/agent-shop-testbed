"""Typer subcommands, one per public function in ``src/string_utils.py``.

Each subcommand takes its input string as a positional argument and maps the
core function's remaining parameters to typed options. An option left unset
on the command line resolves through :func:`src.cli.config.resolve`, which
checks (in order) the ``STRUTIL_<NAME>`` environment variable, the
``[<command>]`` table of the discovered ``strutil.toml``, then the core
function's own default -- so a config value only ever applies when the flag
itself is omitted.
"""

from typing import Annotated

import typer

from .. import string_utils
from .config import load_config, resolve
from .main import app
from .render import EXIT_USAGE, diagnostic, render


def _section(command: str) -> dict:
    """The ``[<command>]`` table of the discovered config file, or ``{}``."""
    section = load_config().get(command, {})
    return section if isinstance(section, dict) else {}


@app.command("reverse")
def reverse_command(
    s: Annotated[str, typer.Argument(help="The string to reverse.")],
) -> None:
    """Reverse a string."""
    render("reverse", True, result=string_utils.reverse(s))


@app.command("is-palindrome")
def is_palindrome_command(
    s: Annotated[str, typer.Argument(help="The string to check for palindrome-ness.")],
) -> None:
    """Check whether a string is a palindrome (case-insensitive, ignoring spaces)."""
    render("is-palindrome", True, result=string_utils.is_palindrome(s))


@app.command("word-count")
def word_count_command(
    s: Annotated[str, typer.Argument(help="The string whose words will be counted.")],
) -> None:
    """Count words in a string."""
    render("word-count", True, result=string_utils.word_count(s))


@app.command("truncate")
def truncate_command(
    s: Annotated[str, typer.Argument(help="The string to truncate.")],
    max_length: Annotated[
        int | None,
        typer.Option(help="The maximum length of the resulting string."),
    ] = None,
    suffix: Annotated[
        str | None,
        typer.Option(help="The suffix appended to the string when it is truncated."),
    ] = None,
) -> None:
    """Truncate a string to a maximum length, adding a suffix if truncated."""
    config = _section("truncate")
    resolved_max_length = resolve("max_length", flag=max_length, config=config)
    if resolved_max_length is None:
        diagnostic("truncate: missing required option '--max-length'")
        raise typer.Exit(EXIT_USAGE)
    resolved_suffix = resolve("suffix", flag=suffix, config=config, default="...")

    render(
        "truncate",
        True,
        result=string_utils.truncate(s, int(resolved_max_length), str(resolved_suffix)),
    )


@app.command("parse-config")
def parse_config_command(
    text: Annotated[
        str,
        typer.Argument(help="Key=value configuration text to parse, ignoring '#' comments."),
    ],
) -> None:
    """Parse key=value configuration text into a dict."""
    render("parse-config", True, result=string_utils.parse_config(text))


@app.command("slugify")
def slugify_command(
    s: Annotated[str, typer.Argument(help="The string to convert into a slug.")],
) -> None:
    """Convert a string into a lowercase, URL-safe slug."""
    render("slugify", True, result=string_utils.slugify(s))


@app.command("title-case")
def title_case_command(
    s: Annotated[str, typer.Argument(help="The string to convert to title case.")],
) -> None:
    """Capitalise each word, keeping short joining words lowercase unless first."""
    render("title-case", True, result=string_utils.title_case(s))
