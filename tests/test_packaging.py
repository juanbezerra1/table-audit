"""What is shipped: the wheel, the version, the command and the CI.

GitHub Actions and Windows are not available everywhere this is written or tested, so these tests check, without
them, the mistakes that would only show on the first real run: a file the wheel forgot, a Python version that the CI
does not try, a command that the workflow runs and that does not give the exit code it expects.
"""

from __future__ import annotations

import importlib
import re
import shlex
import zipfile
from pathlib import Path

import pytest

from table_audit import __version__
from table_audit.cli import EXIT_FOUND, main

from .helpers import ROOT


def pyproject() -> str:
    return (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def ci() -> str:
    return (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_version_is_the_same_in_the_package_the_project_file_and_the_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    declared = re.search(r'^version = "([^"]+)"', pyproject(), re.MULTILINE)
    assert declared is not None and declared.group(1) == __version__
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.strip() == f"table-audit {__version__}"


def test_the_wheel_holds_the_package_and_nothing_else_and_asks_for_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    build = pytest.importorskip("hatchling.build")
    monkeypatch.chdir(ROOT)
    wheel_name = build.build_wheel(str(tmp_path))
    dist_info = f"table_audit-{__version__}.dist-info"
    with zipfile.ZipFile(tmp_path / wheel_name) as wheel:
        names = wheel.namelist()
        metadata = wheel.read(f"{dist_info}/METADATA").decode("utf-8")
        entry_points = wheel.read(f"{dist_info}/entry_points.txt").decode("utf-8")

    outside = [n for n in names if not n.startswith(("table_audit/", f"{dist_info}/"))]
    assert not outside, f"files in the wheel that are not the package: {outside}"
    sources = {p.relative_to(ROOT / "src").as_posix() for p in (ROOT / "src" / "table_audit").rglob("*.py")}
    assert sources and sources <= set(names), f"missing from the wheel: {sorted(sources - set(names))}"
    assert "table_audit/py.typed" in names, "the project says 'Typing :: Typed', so the marker has to ship"
    assert "Name: table-audit" in metadata
    assert f"Version: {__version__}" in metadata
    assert "Requires-Python: >=3.10" in metadata
    needed = [line for line in metadata.splitlines() if line.startswith("Requires-Dist:") and "extra ==" not in line]
    assert needed == [], f"the tool says it has no dependencies, and the wheel asks for {needed}"
    assert "# table-audit" in metadata, "the README is the long description"
    assert "table-audit = table_audit.cli:main" in entry_points


def test_the_command_the_wheel_installs_exists() -> None:
    target = re.search(r'^table-audit = "([\w.]+):(\w+)"', pyproject(), re.MULTILINE)
    assert target is not None
    assert callable(getattr(importlib.import_module(target.group(1)), target.group(2)))


def test_the_package_imports_nothing_that_is_not_in_the_standard_library() -> None:
    import sys

    standard = set(sys.stdlib_module_names)
    outside: list[str] = []
    for path in (ROOT / "src" / "table_audit").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for module in re.findall(r"^(?:from|import) ([a-z_]+)", text, re.MULTILINE):
            if module not in standard and module not in ("table_audit",):
                outside.append(f"{path.name}: {module}")
    assert not outside, f"the README promises the standard library only: {outside}"


def test_the_ci_tests_the_oldest_and_the_newest_python_the_project_claims() -> None:
    matrix = re.search(r"python-version: \[(.*?)\]", ci())
    assert matrix is not None
    versions = [v.strip().strip('"') for v in matrix.group(1).split(",")]
    minimum = re.search(r'^requires-python = ">=(\d+\.\d+)"', pyproject(), re.MULTILINE)
    assert minimum is not None
    claimed = re.findall(r'"Programming Language :: Python :: (3\.\d+)"', pyproject())
    assert versions[0] == minimum.group(1)
    assert versions[-1] == max(claimed, key=lambda v: int(v.split(".")[1]))
    # the tools are told the same minimum
    tag = "py" + minimum.group(1).replace(".", "")
    assert f'target-version = "{tag}"' in pyproject()
    assert f'python_version = "{minimum.group(1)}"' in pyproject()


def test_the_ci_has_the_windows_job_the_readme_talks_about_and_runs_what_the_readme_runs() -> None:
    text = ci()
    assert "windows-latest" in text and 'python-version: "3.13"' in text
    for tool in ("ruff check .", "ruff format --check .", "mypy src tests scripts", "pytest"):
        assert tool in text, f"the CI does not run {tool}"
    for folder in ("src", "tests", "scripts"):
        assert (ROOT / folder).is_dir()


def test_the_commands_the_ci_runs_on_the_examples_give_the_exit_code_it_expects(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    commands = re.findall(r"^\s*table-audit (.+?) \|\| code=\$\?$", ci(), re.MULTILINE)
    assert len(commands) == 2
    for command in commands:
        assert main(shlex.split(command)) == EXIT_FOUND, f"`table-audit {command}` should find errors in the examples"
    capsys.readouterr()


def test_the_gitignore_keeps_the_caches_out() -> None:
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for wanted in ("__pycache__/", ".pytest_cache/", ".ruff_cache/", ".mypy_cache/", "*.egg-info/"):
        assert wanted in patterns
