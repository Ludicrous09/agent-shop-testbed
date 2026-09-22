"""Tests mapping CLI failures onto the exit-code contract: 0, 1, and 2.

Subcommands do not exist yet, so every scenario is exercised against the
bare ``strutil`` app. The hidden ``--_simulate`` flag on the root callback
is internal-only plumbing to reach the operational-failure path (exit 1)
before any real subcommand exists to raise it.
"""

import os

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.cli.render import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_cwd_and_home(tmp_path, monkeypatch):
    """Run every test from an empty directory with no ambient config or env vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    for key in list(os.environ):
        if key.startswith("STRUTIL_"):
            monkeypatch.delenv(key, raising=False)


def test_successful_invocation_exits_zero():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == EXIT_SUCCESS


def test_operational_failure_exits_one_with_message_on_stderr_and_empty_stdout():
    result = runner.invoke(app, ["--_simulate", "operational-error"])

    assert result.exit_code == EXIT_FAILURE
    assert result.stdout == ""
    assert result.stderr != ""


def test_unknown_subcommand_exits_two_with_message_on_stderr_and_empty_stdout():
    result = runner.invoke(app, ["does-not-exist"])

    assert result.exit_code == EXIT_USAGE
    assert result.stdout == ""
    assert result.stderr != ""


def test_bad_flag_value_exits_two_with_message_on_stderr_and_empty_stdout():
    result = runner.invoke(app, ["--_simulate", "not-a-real-value"])

    assert result.exit_code == EXIT_USAGE
    assert result.stdout == ""
    assert result.stderr != ""


def test_malformed_config_file_exits_two_with_message_on_stderr_and_empty_stdout(tmp_path):
    (tmp_path / "strutil.toml").write_text("not = valid = toml")

    result = runner.invoke(app, ["--_simulate", "none"])

    assert result.exit_code == EXIT_USAGE
    assert result.stdout == ""
    assert result.stderr != ""
