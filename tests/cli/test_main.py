"""Tests for the ``strutil`` CLI skeleton: app object, entry points, packaging."""

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from src.cli.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_DIR = REPO_ROOT / "src" / "cli"

FORBIDDEN_IMPORT_ROOTS = {
    "pydantic",
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
    "requests",
    "urllib3",
    "aiohttp",
    "httplib2",
}
FORBIDDEN_IMPORTS = {"http.client", "urllib.request"}

runner = CliRunner()


def _imported_modules(path: Path) -> set[str]:
    """Absolute dotted names imported by ``path``, treating it as a ``src.cli`` module."""
    package = ["src", "cli"]
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - (node.level - 1)] if node.level else []
            prefix = ".".join([*base, *([node.module] if node.module else [])])
            modules.add(prefix)
            modules.update(f"{prefix}.{alias.name}" for alias in node.names)
    return modules


def test_help_exits_zero_and_shows_help_text():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert app.info.help
    assert app.info.help in result.stdout


def test_expected_subcommands_are_registered():
    registered_names = {command.name for command in app.registered_commands}
    assert registered_names == {
        "reverse",
        "is-palindrome",
        "word-count",
        "truncate",
        "parse-config",
        "slugify",
        "title-case",
    }
    assert app.registered_groups == []


def test_cli_modules_do_not_import_api_pydantic_or_http_clients():
    paths = sorted(CLI_DIR.glob("*.py"))
    assert {p.name for p in paths} >= {"__init__.py", "main.py", "__main__.py"}

    offenders = []
    for path in paths:
        for module in _imported_modules(path):
            root = module.split(".")[0]
            in_api = module.split(".")[:2] in (["src", "api"], ["strutil", "api"])
            if in_api or root in FORBIDDEN_IMPORT_ROOTS or module in FORBIDDEN_IMPORTS:
                offenders.append(f"{path.name}: {module}")

    assert offenders == []


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    """A non-editable ``pip install .`` of a copy of the repo, into a scratch directory.

    Installing a copy keeps setuptools' ``build/`` and ``*.egg-info`` out of the working
    tree. Returns ``(site, run)`` where ``run(*argv)`` executes a command with only the
    installed copy importable, from a directory that is not the repo root.
    """
    root = tmp_path_factory.mktemp("install")
    source = root / "source"
    site = root / "site"
    workdir = root / "workdir"
    workdir.mkdir()
    shutil.copytree(REPO_ROOT / "src", source / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO_ROOT / "pyproject.toml", source / "pyproject.toml")

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(site), str(source)],
        check=True,
        capture_output=True,
        text=True,
    )

    env = {**os.environ, "PYTHONPATH": str(site)}

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(argv, cwd=workdir, env=env, capture_output=True, text=True)

    return site, run


def test_non_editable_install_ships_cli_package_under_both_names(installed):
    site, run = installed

    for top in ("src", "strutil"):
        for name in ("__init__.py", "main.py", "__main__.py"):
            assert (site / top / "cli" / name).is_file(), f"{top}/cli/{name} missing"
    assert (site / "strutil" / "__main__.py").is_file()

    for module in ("src.cli.main", "strutil.cli.main"):
        result = run(sys.executable, "-c", f"import {module}")
        assert result.returncode == 0, result.stderr


def test_python_m_strutil_help_matches_installed_command(installed):
    site, run = installed

    module = run(sys.executable, "-m", "strutil", "--help")
    command = run(str(site / "bin" / "strutil"), "--help")

    assert module.returncode == 0, module.stderr
    assert command.returncode == 0, command.stderr
    assert app.info.help in module.stdout
    assert module.stdout == command.stdout


def test_python_m_strutil_cli_help_matches_installed_command(installed):
    site, run = installed

    module = run(sys.executable, "-m", "strutil.cli", "--help")
    command = run(str(site / "bin" / "strutil"), "--help")

    assert module.returncode == 0, module.stderr
    assert module.stdout == command.stdout
