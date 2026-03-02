"""Generate a CLAUDE.md file for a target repository by analyzing its codebase."""

import argparse
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _read_file_safe(path: Path, max_bytes: int = 8192) -> str:
    """Read a file safely, returning empty string on error."""
    try:
        content = path.read_bytes()
        return content[:max_bytes].decode("utf-8", errors="replace")
    except OSError:
        return ""


def detect_languages(repo_path: Path) -> list[str]:
    """Detect programming languages used in the repo."""
    languages: list[str] = []

    indicators: list[tuple[str, str]] = [
        ("package.json", "JavaScript/TypeScript"),
        ("requirements.txt", "Python"),
        ("pyproject.toml", "Python"),
        ("setup.py", "Python"),
        ("setup.cfg", "Python"),
        ("Cargo.toml", "Rust"),
        ("go.mod", "Go"),
        ("pom.xml", "Java"),
        ("build.gradle", "Java/Kotlin"),
        ("*.gemspec", "Ruby"),
        ("Gemfile", "Ruby"),
        ("composer.json", "PHP"),
        ("*.csproj", "C#"),
    ]

    for filename, lang in indicators:
        if "*" in filename:
            if list(repo_path.glob(filename)):
                languages.append(lang)
        elif (repo_path / filename).exists():
            languages.append(lang)

    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for lang in languages:
        if lang not in seen:
            seen.add(lang)
            result.append(lang)
    return result


def detect_frameworks(repo_path: Path) -> list[str]:
    """Detect frameworks from manifest files."""
    frameworks: list[str] = []

    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            deps: dict[str, str] = {}
            deps.update(data.get("dependencies", {}))
            deps.update(data.get("devDependencies", {}))

            framework_map = {
                "react": "React",
                "vue": "Vue.js",
                "next": "Next.js",
                "@angular/core": "Angular",
                "express": "Express",
                "fastify": "Fastify",
                "svelte": "Svelte",
                "nuxt": "Nuxt.js",
            }
            for dep_key, fw_name in framework_map.items():
                if dep_key in deps:
                    frameworks.append(fw_name)
        except (json.JSONDecodeError, OSError):
            pass

    # Python frameworks via requirements.txt / pyproject.toml
    for req_file in ["requirements.txt", "requirements-dev.txt"]:
        req_path = repo_path / req_file
        if req_path.exists():
            content = _read_file_safe(req_path).lower()
            fw_map = {
                "django": "Django",
                "flask": "Flask",
                "fastapi": "FastAPI",
                "tornado": "Tornado",
                "starlette": "Starlette",
            }
            for key, name in fw_map.items():
                pattern = r"(?i)^" + re.escape(key) + r"[^a-zA-Z0-9_-]"
                if re.search(pattern, content, re.MULTILINE):
                    frameworks.append(name)

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        content = _read_file_safe(pyproject).lower()
        fw_map = {
            "django": "Django",
            "flask": "Flask",
            "fastapi": "FastAPI",
        }
        for key, name in fw_map.items():
            if key in content and name not in frameworks:
                frameworks.append(name)

    return frameworks


def detect_test_framework(repo_path: Path) -> str | None:
    """Detect the test framework used."""
    # Python
    for cfg_file in ["pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"]:
        path = repo_path / cfg_file
        if path.exists():
            content = _read_file_safe(path).lower()
            if "pytest" in content:
                return "pytest"

    if (repo_path / "requirements.txt").exists():
        content = _read_file_safe(repo_path / "requirements.txt").lower()
        if "pytest" in content:
            return "pytest"

    # JavaScript / Node
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            deps: dict[str, str] = {}
            deps.update(data.get("dependencies", {}))
            deps.update(data.get("devDependencies", {}))
            scripts: dict[str, str] = data.get("scripts", {})

            for dep in deps:
                if "jest" in dep:
                    return "Jest"
            for dep in deps:
                if "mocha" in dep:
                    return "Mocha"
            for dep in deps:
                if "vitest" in dep:
                    return "Vitest"

            test_script = scripts.get("test", "").lower()
            if "jest" in test_script:
                return "Jest"
            if "mocha" in test_script:
                return "Mocha"
            if "vitest" in test_script:
                return "Vitest"
        except (json.JSONDecodeError, OSError):
            pass

    # Rust
    if (repo_path / "Cargo.toml").exists():
        return "cargo test"

    # Go
    if (repo_path / "go.mod").exists():
        return "go test"

    return None


def detect_linter(repo_path: Path) -> dict[str, str]:
    """Detect linter configuration. Returns {tool_name: config_summary}."""
    linters: dict[str, str] = {}

    linter_files = [
        (".eslintrc", "ESLint"),
        (".eslintrc.js", "ESLint"),
        (".eslintrc.json", "ESLint"),
        (".eslintrc.yaml", "ESLint"),
        (".eslintrc.yml", "ESLint"),
        ("eslint.config.js", "ESLint"),
        ("eslint.config.mjs", "ESLint"),
        (".flake8", "Flake8"),
        ("ruff.toml", "Ruff"),
        (".ruff.toml", "Ruff"),
        (".pylintrc", "Pylint"),
        ("mypy.ini", "mypy"),
        (".mypy.ini", "mypy"),
        ("tslint.json", "TSLint"),
        (".prettierrc", "Prettier"),
        (".prettierrc.json", "Prettier"),
        ("prettier.config.js", "Prettier"),
        ("clippy.toml", "Clippy"),
    ]

    for filename, tool in linter_files:
        path = repo_path / filename
        if path.exists():
            if tool not in linters:
                linters[tool] = filename

    # Check pyproject.toml for ruff/flake8/mypy sections
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        content = _read_file_safe(pyproject)
        for tool, section in [("Ruff", "[tool.ruff]"), ("mypy", "[tool.mypy]"), ("Pylint", "[tool.pylint")]:
            if section in content and tool not in linters:
                linters[tool] = "pyproject.toml"

    return linters


def detect_build_commands(repo_path: Path) -> list[str]:
    """Detect common build/run commands."""
    commands: list[str] = []

    # package.json scripts
    pkg_json = repo_path / "package.json"
    if pkg_json.exists():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            scripts: dict[str, str] = data.get("scripts", {})
            for name in ["build", "start", "dev", "test", "lint"]:
                if name in scripts:
                    commands.append(f"npm run {name}")
        except (json.JSONDecodeError, OSError):
            pass

    # Python
    if (repo_path / "requirements.txt").exists():
        commands.append("pip install -r requirements.txt")
    if (repo_path / "pyproject.toml").exists():
        content = _read_file_safe(repo_path / "pyproject.toml")
        if "pytest" in content.lower():
            commands.append("pytest")
        if "ruff" in content.lower():
            commands.append("ruff check .")

    # Makefile
    makefile = repo_path / "Makefile"
    if makefile.exists():
        content = _read_file_safe(makefile)
        targets = []
        for line in content.splitlines():
            if line and not line.startswith("\t") and not line.startswith("#"):
                if ":" in line:
                    target = line.split(":")[0].strip()
                    if target and not target.startswith(".") and re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', target):
                        targets.append(target)
        if targets:
            commands.append(f"make  # targets: {', '.join(targets[:8])}")

    # Cargo
    if (repo_path / "Cargo.toml").exists():
        commands.extend(["cargo build", "cargo test", "cargo run"])

    # Go
    if (repo_path / "go.mod").exists():
        commands.extend(["go build ./...", "go test ./..."])

    return commands


def detect_directory_structure(repo_path: Path) -> list[str]:
    """Return notable top-level directories and files."""
    notable: list[str] = []
    common_dirs = [
        "src", "lib", "tests", "test", "spec", "docs", "scripts",
        "config", "pkg", "cmd", "internal", "public", "static",
        "assets", "components", "pages", "api", "models", "utils",
        "migrations", "fixtures",
    ]
    for d in common_dirs:
        if (repo_path / d).is_dir():
            notable.append(f"{d}/")

    common_files = [
        "README.md", "CLAUDE.md", "PLAN.yaml", "Makefile",
        "docker-compose.yml", "Dockerfile", ".env.example",
    ]
    for f in common_files:
        if (repo_path / f).exists():
            notable.append(f)

    return notable


def read_readme(repo_path: Path, max_chars: int = 2000) -> str:
    """Read the README.md (or README.rst) from the repo."""
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        path = repo_path / name
        if path.exists():
            return _read_file_safe(path, max_bytes=max_chars)
    return ""


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def generate_claude_md(repo_path: Path) -> str:
    """Analyze the repo and return the content of a CLAUDE.md file."""
    languages = detect_languages(repo_path)
    frameworks = detect_frameworks(repo_path)
    test_fw = detect_test_framework(repo_path)
    linters = detect_linter(repo_path)
    commands = detect_build_commands(repo_path)
    dirs = detect_directory_structure(repo_path)
    readme = read_readme(repo_path)

    sections: list[str] = []

    # Project overview
    sections.append("# Project Overview\n")
    if readme:
        # Use first non-empty paragraph of README as summary
        paragraphs = [p.strip() for p in readme.split("\n\n") if p.strip()]
        summary = paragraphs[0] if paragraphs else ""
        # Strip leading markdown heading if present
        if summary.startswith("#"):
            lines = summary.splitlines()
            # Use heading as title, rest as body
            title_line = lines[0].lstrip("#").strip()
            body_lines = [ln for ln in lines[1:] if ln.strip()]
            if body_lines:
                summary = title_line + "\n\n" + "\n".join(body_lines[:5])
            else:
                summary = title_line
        else:
            # Limit to ~300 chars
            if len(summary) > 300:
                summary = summary[:300].rsplit(" ", 1)[0] + "..."
        sections.append(summary + "\n")
    else:
        sections.append(f"Repository at `{repo_path.name}`.\n")

    # Tech stack
    sections.append("\n## Tech Stack\n")
    if languages:
        sections.append(f"**Languages:** {', '.join(languages)}\n")
    if frameworks:
        sections.append(f"**Frameworks:** {', '.join(frameworks)}\n")
    if test_fw:
        sections.append(f"**Test framework:** {test_fw}\n")
    if linters:
        sections.append(f"**Linters/formatters:** {', '.join(linters.keys())}\n")
    if not languages and not frameworks:
        sections.append("_(Could not auto-detect tech stack)_\n")

    # Directory structure
    if dirs:
        sections.append("\n## Directory Structure\n")
        sections.append("Notable directories and files:\n")
        for d in dirs:
            sections.append(f"- `{d}`\n")

    # Code conventions
    if linters:
        sections.append("\n## Code Conventions\n")
        for tool, cfg in linters.items():
            sections.append(f"- **{tool}**: configured in `{cfg}`\n")
        if "Ruff" in linters:
            sections.append("- Run `ruff check .` before committing\n")
        if "ESLint" in linters:
            sections.append("- Run `eslint .` before committing\n")
        if "Prettier" in linters:
            sections.append("- Run `prettier --check .` before committing\n")

    # Test conventions
    sections.append("\n## Test Conventions\n")
    if test_fw:
        sections.append(f"- **Framework:** {test_fw}\n")
        if test_fw == "pytest":
            test_dirs = [d for d in ["tests/", "test/"] if (repo_path / d.rstrip("/")).is_dir()]
            if test_dirs:
                sections.append(f"- Test location: `{'`, `'.join(test_dirs)}`\n")
            sections.append("- Test files follow `test_*.py` naming convention\n")
            sections.append("- Run: `pytest`\n")
        elif test_fw in ("Jest", "Mocha", "Vitest"):
            test_dirs_js = [d for d in ["tests/", "test/", "__tests__/"] if (repo_path / d.rstrip("/")).is_dir()]
            if test_dirs_js:
                sections.append(f"- Test location: `{'`, `'.join(test_dirs_js)}`\n")
            sections.append("- Run: `npm test`\n")
        elif test_fw == "cargo test":
            sections.append("- Tests live in `src/` alongside source, or in `tests/`\n")
            sections.append("- Run: `cargo test`\n")
        elif test_fw == "go test":
            sections.append("- Test files follow `*_test.go` naming convention\n")
            sections.append("- Run: `go test ./...`\n")
    else:
        sections.append("_(No test framework detected)_\n")

    # Build/run commands
    if commands:
        sections.append("\n## Build / Run Commands\n")
        for cmd in commands:
            sections.append(f"```\n{cmd}\n```\n")

    return "".join(sections)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run(repo_path: Path, force: bool = False) -> bool:
    """Generate CLAUDE.md for the repo.

    Returns True if the file was written, False if skipped (already exists).
    """
    target = repo_path / "CLAUDE.md"
    if target.exists() and not force:
        logger.info("CLAUDE.md already exists at %s — skipping", target)
        return False

    logger.info("Analyzing repo at %s", repo_path)
    content = generate_claude_md(repo_path)
    target.write_text(content, encoding="utf-8")
    logger.info("Wrote CLAUDE.md to %s (%d bytes)", target, len(content))
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-generate a CLAUDE.md for a target repository"
    )
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Path to the repository root (default: current directory)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite CLAUDE.md even if it already exists",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )

    repo = Path(args.repo_path).resolve()
    written = run(repo, force=args.force)
    if written:
        print(f"Generated CLAUDE.md at {repo / 'CLAUDE.md'}")
    else:
        print(f"Skipped: CLAUDE.md already exists at {repo / 'CLAUDE.md'}")


if __name__ == "__main__":
    main()
