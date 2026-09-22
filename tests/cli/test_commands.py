"""Tests for the ``strutil`` subcommands: one per public string utility."""

import os

import pytest
from typer.testing import CliRunner

from src import string_utils
from src.cli.main import app

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


def test_reverse():
    result = runner.invoke(app, ["reverse", "hello"])

    assert result.stdout == f"{string_utils.reverse('hello')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_is_palindrome():
    result = runner.invoke(app, ["is-palindrome", "A man a plan a canal Panama"])

    assert result.stdout == f"{string_utils.is_palindrome('A man a plan a canal Panama')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_word_count():
    result = runner.invoke(app, ["word-count", "the quick brown fox"])

    assert result.stdout == f"{string_utils.word_count('the quick brown fox')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_truncate():
    result = runner.invoke(app, ["truncate", "hello world", "--max-length", "5"])

    assert result.stdout == f"{string_utils.truncate('hello world', 5)}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_truncate_with_explicit_suffix():
    result = runner.invoke(app, ["truncate", "hello world", "--max-length", "5", "--suffix", "!!"])

    assert result.stdout == f"{string_utils.truncate('hello world', 5, '!!')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_truncate_without_max_length_anywhere_is_a_usage_error():
    result = runner.invoke(app, ["truncate", "hello world"])

    assert result.stdout == ""
    assert result.stderr != ""
    assert result.exit_code == 2


def test_parse_config():
    text = "a=1\n# comment\nb=2\n"
    result = runner.invoke(app, ["parse-config", text])

    assert result.stdout == f"{string_utils.parse_config(text)}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_slugify():
    result = runner.invoke(app, ["slugify", "Hello, World!"])

    assert result.stdout == f"{string_utils.slugify('Hello, World!')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_title_case():
    result = runner.invoke(app, ["title-case", "the lord of the rings"])

    assert result.stdout == f"{string_utils.title_case('the lord of the rings')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_toml_default_applies_when_flag_omitted(tmp_path):
    (tmp_path / "strutil.toml").write_text("[truncate]\nmax_length = 5\nsuffix = '~'\n")

    result = runner.invoke(app, ["truncate", "hello world"])

    assert result.stdout == f"{string_utils.truncate('hello world', 5, '~')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0


def test_explicit_flag_overrides_toml_default(tmp_path):
    (tmp_path / "strutil.toml").write_text("[truncate]\nmax_length = 5\nsuffix = '~'\n")

    result = runner.invoke(app, ["truncate", "hello world", "--max-length", "8", "--suffix", "!!"])

    assert result.stdout == f"{string_utils.truncate('hello world', 8, '!!')}\n"
    assert result.stderr == ""
    assert result.exit_code == 0
