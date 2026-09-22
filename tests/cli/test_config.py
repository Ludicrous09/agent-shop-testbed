"""Tests for TOML config discovery, loading, and flag-resolution precedence."""

import os

import pytest

from src.cli.config import ConfigError, find_config_path, load_config, resolve


@pytest.fixture(autouse=True)
def _isolated_cwd_and_home(tmp_path, monkeypatch):
    """Run every test from an empty directory with no ambient config or env vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    for key in list(os.environ):
        if key.startswith("STRUTIL_"):
            monkeypatch.delenv(key, raising=False)


def test_missing_config_file_yields_empty_defaults():
    assert find_config_path() is None
    assert load_config() == {}


def test_malformed_config_file_raises_config_error(tmp_path):
    (tmp_path / "strutil.toml").write_text("not = valid = toml")

    with pytest.raises(ConfigError):
        load_config()


def test_project_local_config_is_discovered(tmp_path):
    (tmp_path / "strutil.toml").write_text('format = "json"\n')

    assert find_config_path().resolve() == tmp_path / "strutil.toml"
    assert load_config() == {"format": "json"}


def test_xdg_config_home_is_discovered_when_no_local_file(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    (xdg / "strutil").mkdir(parents=True)
    (xdg / "strutil" / "config.toml").write_text('format = "text"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert find_config_path() == xdg / "strutil" / "config.toml"
    assert load_config() == {"format": "text"}


def test_project_local_file_preferred_over_xdg_when_both_exist(tmp_path, monkeypatch):
    (tmp_path / "strutil.toml").write_text('format = "local"\n')
    xdg = tmp_path / "xdg"
    (xdg / "strutil").mkdir(parents=True)
    (xdg / "strutil" / "config.toml").write_text('format = "xdg"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert find_config_path().resolve() == tmp_path / "strutil.toml"
    assert load_config() == {"format": "local"}


def test_home_config_used_when_xdg_config_home_unset(tmp_path):
    home_config_dir = tmp_path / "home" / ".config" / "strutil"
    home_config_dir.mkdir(parents=True)
    (home_config_dir / "config.toml").write_text('format = "home"\n')

    assert find_config_path() == home_config_dir / "config.toml"
    assert load_config() == {"format": "home"}


def test_resolve_uses_built_in_default_when_nothing_else_set():
    assert resolve("format", default="text") == "text"


def test_resolve_config_value_wins_over_default():
    assert resolve("format", config={"format": "json"}, default="text") == "json"


def test_resolve_env_var_wins_over_config_and_default(monkeypatch):
    monkeypatch.setenv("STRUTIL_FORMAT", "env")

    assert resolve("format", config={"format": "json"}, default="text") == "env"


def test_resolve_flag_wins_over_env_config_and_default(monkeypatch):
    monkeypatch.setenv("STRUTIL_FORMAT", "env")

    assert resolve("format", flag="flag", config={"format": "json"}, default="text") == "flag"
