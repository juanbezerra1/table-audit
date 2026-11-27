"""Shared by the tests: tables made from text, and findings looked up by name."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from table_audit.reader import Table, parse_table
from table_audit.report import Finding

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
_scripts: dict[str, ModuleType] = {}


def load_script(name: str) -> ModuleType:
    """Import scripts/<name>.py, which is not a package (the tests of the documents use it)."""
    if name not in _scripts:
        spec = importlib.util.spec_from_file_location(f"scripts_{name}", ROOT / "scripts" / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        _scripts[name] = module
    return _scripts[name]


def make(
    text: str,
    name: str = "t.csv",
    *,
    delimiter: str | None = None,
    decimal: str | None = None,
    missing: tuple[str, ...] | None = None,
) -> Table:
    """A table from the text of a CSV file (the rows are separated by newlines)."""
    return parse_table(text.encode("utf-8"), name, delimiter=delimiter, decimal=decimal, missing=missing)


def named(findings: list[Finding], check: str, column: str | None = None) -> list[Finding]:
    """The findings of one check (and, when given, of one column)."""
    return [f for f in findings if f.check == check and (column is None or f.column == column)]


def one(findings: list[Finding], check: str, column: str | None = None) -> Finding:
    """The only finding of a check; the test fails, and says what there was, if there are none or several."""
    found = named(findings, check, column)
    assert len(found) == 1, f"wanted one {check!r} finding, there are {[(f.check, f.column) for f in findings]}"
    return found[0]


def checks(findings: list[Finding]) -> list[str]:
    return [f.check for f in findings]
