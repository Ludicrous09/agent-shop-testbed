"""Entry point for ``python -m strutil``.

``pyproject.toml`` maps the ``strutil`` package onto the ``src`` directory, so
this file is ``strutil/__main__.py`` once installed.
"""

from .cli.main import app

if __name__ == "__main__":
    # ``prog_name`` is pinned so the usage line matches the installed
    # ``strutil`` command instead of reading "python -m strutil".
    app(prog_name="strutil")
