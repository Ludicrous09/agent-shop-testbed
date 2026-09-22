"""TOML-backed defaults for CLI flags.

Discovery order (first match wins):

1. ``./strutil.toml`` — a project-local file, so a repository can check in
   its own defaults for scripting.
2. ``$XDG_CONFIG_HOME/strutil/config.toml``
3. ``~/.config/strutil/config.toml`` — used when ``XDG_CONFIG_HOME`` is unset.

A missing config file at every location is not an error: it yields empty
defaults. A malformed one is a usage error, raised as :class:`ConfigError`.

Resolution order for a single value (first present wins):

1. An explicit command-line flag.
2. The ``STRUTIL_<NAME>`` environment variable.
3. The config file.
4. The built-in default.

This module never prints or exits; callers map :class:`ConfigError` to the
process exit code.
"""

import os
import tomllib
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the discovered config file cannot be parsed as TOML."""


def find_config_path() -> Path | None:
    """Return the first config file that exists, in discovery order, or ``None``."""
    local = Path("strutil.toml")
    if local.is_file():
        return local

    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_home) if xdg_home else Path.home() / ".config"
    candidate = base / "strutil" / "config.toml"
    if candidate.is_file():
        return candidate

    return None


def load_config() -> dict[str, Any]:
    """Load flag defaults from the discovered config file.

    Returns an empty dict when no config file is found. Raises
    :class:`ConfigError` when the discovered file is not valid TOML.
    """
    path = find_config_path()
    if path is None:
        return {}

    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Malformed config file at {path}: {exc}") from exc


def resolve(
    name: str,
    *,
    flag: Any = None,
    config: dict[str, Any] | None = None,
    default: Any = None,
) -> Any:
    """Resolve a single flag's value: flag > ``STRUTIL_<NAME>`` env var > config > default."""
    if flag is not None:
        return flag

    env_value = os.environ.get(f"STRUTIL_{name.upper()}")
    if env_value is not None:
        return env_value

    if config is not None and name in config:
        return config[name]

    return default
