"""Entry point for ``python -m strutil.cli``."""

from .main import app

if __name__ == "__main__":
    # ``prog_name`` is pinned so the usage line matches the installed
    # ``strutil`` command instead of reading "python -m strutil.cli".
    app(prog_name="strutil")
