"""Tests for the single CLI output-rendering module."""

import json

from src.cli.render import EXIT_FAILURE, EXIT_SUCCESS, EXIT_USAGE, diagnostic, render


def test_exit_code_constants():
    assert EXIT_SUCCESS == 0
    assert EXIT_FAILURE == 1
    assert EXIT_USAGE == 2


def test_json_mode_stdout_is_single_json_object_with_exact_keys(capsys):
    render("greet", True, result={"message": "hi"}, format="json")

    captured = capsys.readouterr()
    assert captured.err == ""
    parsed = json.loads(captured.out)
    assert set(parsed.keys()) == {"command", "ok", "result", "error"}


def test_json_mode_stdout_has_single_trailing_newline_and_nothing_else(capsys):
    render("greet", True, result="hi", format="json")

    captured = capsys.readouterr()
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    json.loads(captured.out)


def test_json_mode_success_sets_ok_true_and_error_null(capsys):
    render("greet", True, result="hi", error=None, format="json")

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["command"] == "greet"
    assert parsed["ok"] is True
    assert parsed["result"] == "hi"
    assert parsed["error"] is None


def test_json_mode_failure_sets_ok_false_and_populates_error(capsys):
    render("greet", False, result=None, error="boom", format="json")

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["ok"] is False
    assert parsed["error"] == "boom"


def test_diagnostic_writes_only_to_stderr(capsys):
    diagnostic("careful: something happened")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "careful: something happened" in captured.err


def test_text_mode_success_writes_result_line(capsys):
    render("greet", True, result="hi")

    captured = capsys.readouterr()
    assert captured.out == "hi\n"
    assert captured.err == ""


def test_text_mode_success_with_no_result_writes_nothing(capsys):
    render("greet", True, result=None)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_text_mode_failure_writes_command_and_error(capsys):
    render("greet", False, error="boom")

    captured = capsys.readouterr()
    assert captured.out == "greet: boom\n"


def test_text_mode_failure_with_no_error_falls_back_to_unknown_error(capsys):
    render("greet", False, error=None)

    captured = capsys.readouterr()
    assert captured.out == "greet: unknown error\n"
