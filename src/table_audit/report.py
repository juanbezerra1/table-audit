"""What an audit finds, and how it is shown.

The text report is plain ASCII on purpose: a Windows console that is not set to UTF-8, or a file it is redirected to,
would turn anything fancier into question marks. The JSON report is for other programs (a CI job, a notebook).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES = ("error", "warning", "info")
MAX_EXAMPLES = 5  # lines and values listed for one finding; `count` has the real number


@dataclass(frozen=True)
class CheckInfo:
    """What one check is: how serious it is, which commands make it, and what it looks for."""

    severity: str
    commands: str
    meaning: str


# Every check there is. A finding names one of these and carries its severity, so this table is what the report, the
# command `table-audit explain` and the README all say; tests/test_docs.py compares the README with it.
CHECKS: dict[str, CheckInfo] = {
    "encoding": CheckInfo("info", "profile, check", "the file is not UTF-8; it was read as the Windows code page"),
    "column-name": CheckInfo("error", "profile, check", "a column without a name, or a name used by two columns"),
    "ragged-rows": CheckInfo("error", "profile, check", "rows with more or fewer cells than the header"),
    "blank-lines": CheckInfo("info", "profile, check", "blank lines in the file (they are skipped)"),
    "empty-rows": CheckInfo("warning", "profile, check", "rows that have no value at all"),
    "no-rows": CheckInfo("warning", "profile, check", "a header and nothing under it"),
    "empty-column": CheckInfo("warning", "profile", "a column with no value in any row"),
    "mixed-types": CheckInfo(
        "warning", "profile", "numbers, dates or true/false with other things among them, or no clear type"
    ),
    "missing": CheckInfo("warning", "profile", "half of the rows, or more, have no value in a column"),
    "constant": CheckInfo("info", "profile", "the same value in every row"),
    "whitespace": CheckInfo("warning", "profile", "spaces at the start or the end of a value"),
    "label-variants": CheckInfo("warning", "profile", "one label written in more than one way (case, accents, spaces)"),
    "placeholder": CheckInfo(
        "warning", "profile", "-999, 9999 and the like: a code for 'missing' used as if it were a measurement"
    ),
    "outliers": CheckInfo("info", "profile", "values more than 6 robust deviations away from the median"),
    "date-formats": CheckInfo("warning", "profile", "dates written two ways in one column (2026-03-02 and 02/03/2026)"),
    "duplicate-rows": CheckInfo("warning", "profile", "a row that repeats an earlier row exactly"),
    "duplicate-key": CheckInfo("error", "profile, check", "a key (--key) or a column that must be unique, and is not"),
    "missing-column": CheckInfo("error", "check", "the rules ask for a column that the table does not have"),
    "extra-column": CheckInfo("error", "check", "a column that the rules do not mention (no_extra_columns)"),
    "not-null": CheckInfo("error", "check", "an empty cell in a column that must have a value"),
    "wrong-type": CheckInfo("error", "check", "a value that is not of the type the rules give the column"),
    "out-of-range": CheckInfo("error", "check", "a number or date below the minimum or above the maximum"),
    "not-allowed": CheckInfo("error", "check", "a value that is not in the list of allowed values"),
    "pattern": CheckInfo("error", "check", "a value that does not match the regular expression of the column"),
    "columns-differ": CheckInfo("info", "leakage", "columns that only one of the two files has"),
    "group-overlap": CheckInfo("error", "leakage", "the same subject (or patient, or device) in training and in test"),
    "identical-rows": CheckInfo("error", "leakage", "a test row that is a training row"),
    "target": CheckInfo(
        "info", "leakage", "too few rows, or a target that is neither a label nor a quantity: its checks are skipped"
    ),
    "id-like-column": CheckInfo("warning", "leakage", "a text column where every value is different"),
    "target-determined": CheckInfo(
        "warning", "leakage", "a column whose every value goes with a single class of the target"
    ),
    "target-separated": CheckInfo(
        "warning", "leakage", "a column that one threshold turns into a perfect classifier of two classes"
    ),
    "target-copy": CheckInfo("warning", "leakage", "a column that is almost a copy of a numeric target"),
    "time-order": CheckInfo("warning", "leakage", "test rows dated before the end of the training data"),
}


@dataclass(frozen=True)
class Finding:
    """One thing worth knowing about a table.

    `check` is a short stable name (a program can match on it), `count` how many rows or cells are affected, `lines`
    the first lines of the file where it happens, `examples` the first values.
    """

    check: str
    severity: str
    message: str
    column: str | None = None
    count: int = 0
    lines: tuple[int, ...] = ()
    examples: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, not {self.severity!r}")
        known = CHECKS.get(self.check)
        if known is None:
            raise ValueError(f"{self.check!r} is not in CHECKS; add it there (and to the README table)")
        if known.severity != self.severity:
            raise ValueError(f"{self.check!r} is a {known.severity} in CHECKS, not a {self.severity}")


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    kind: str  # int, float, date, bool, text, mixed or empty
    missing: int
    distinct: int
    minimum: str = ""
    maximum: str = ""


@dataclass
class Report:
    command: str  # profile, check or leakage
    sources: list[str]
    facts: list[tuple[str, str]] = field(default_factory=list)
    columns: list[ColumnProfile] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def sorted_findings(self) -> list[Finding]:
        """Errors first, then warnings, then information; inside each, in the order the checks found them."""
        return sorted(self.findings, key=lambda f: SEVERITIES.index(f.severity))

    def counts(self) -> dict[str, int]:
        return {s: sum(1 for f in self.findings if f.severity == s) for s in SEVERITIES}

    def failed(self, strict: bool = False) -> bool:
        """Errors fail a run; with `strict`, warnings do too."""
        counts = self.counts()
        return counts["error"] > 0 or (strict and counts["warning"] > 0)


def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def counted(n: int, one: str, many: str) -> str:
    """The number with the words that agree with it: counted(1, 'row has', 'rows have') is '1 row has'."""
    return f"{n} {one if n == 1 else many}"


def where(finding: Finding) -> str:
    """The part in brackets after a message: the lines and the first values."""
    parts = []
    if finding.lines:
        listed = ", ".join(str(n) for n in finding.lines)
        more = finding.count - len(finding.lines)
        word = "line" if len(finding.lines) == 1 and more <= 0 else "lines"
        parts.append(f"{word} {listed}" + (f" and {more} more" if more > 0 else ""))
    if finding.examples:
        parts.append("e.g. " + ", ".join(f"'{e}'" for e in finding.examples))
    return f" ({'; '.join(parts)})" if parts else ""


def render_text(report: Report, version: str, strict: bool = False) -> str:
    out = [f"table-audit {version}: {report.command}", ""]
    width = max((len(label) for label, _ in report.facts), default=0)
    out += [f"{label:<{width}}  {value}" for label, value in report.facts]

    if report.columns:
        rows = [("column", "kind", "missing", "distinct", "min", "max")]
        rows += [(c.name, c.kind, str(c.missing), str(c.distinct), c.minimum, c.maximum) for c in report.columns]
        sizes = [max(len(r[i]) for r in rows) for i in range(6)]
        out += ["", "columns"]
        for number, row in enumerate(rows):
            cells = [
                row[0].ljust(sizes[0]),
                row[1].ljust(sizes[1]),
                row[2].rjust(sizes[2]),
                row[3].rjust(sizes[3]),
                row[4].rjust(sizes[4]),
                row[5].rjust(sizes[5]),
            ]
            out.append("  " + "  ".join(cells).rstrip())
            if number == 0:
                out.append("  " + "  ".join("-" * s for s in sizes).rstrip())

    counts = report.counts()
    out.append("")
    if report.findings:
        summary = ", ".join(plural(counts[s], s) for s in SEVERITIES if counts[s])
        out.append(f"findings: {summary}")
        for finding in report.sorted_findings():
            where_ = f"{finding.column}: " if finding.column else ""
            out.append(f"  {finding.severity:<8}{finding.check:<20}{where_}{finding.message}{where(finding)}")
    else:
        out.append("findings: none")

    out.append("")
    if report.failed(strict):
        shown = [plural(counts["error"], "error")] if counts["error"] else []
        if strict and counts["warning"]:
            shown.append(plural(counts["warning"], "warning"))
        out.append(f"result: FAILED ({', '.join(shown)})")
    else:
        extra = f" ({plural(counts['warning'], 'warning')})" if counts["warning"] else ""
        out.append(f"result: ok{extra}")
    return "\n".join(out) + "\n"


def render_json(report: Report, version: str, strict: bool = False) -> str:
    payload: dict[str, Any] = {
        "tool": "table-audit",
        "version": version,
        "command": report.command,
        "sources": report.sources,
        "facts": dict(report.facts),
        "columns": [asdict(c) for c in report.columns],
        "findings": [
            {
                "check": f.check,
                "severity": f.severity,
                "column": f.column,
                "message": f.message,
                "count": f.count,
                "lines": list(f.lines),
                "examples": list(f.examples),
            }
            for f in report.sorted_findings()
        ],
        "summary": {**report.counts(), "failed": report.failed(strict)},
    }
    return json.dumps(payload, indent=2) + "\n"


def render_checks(version: str, name: str | None = None) -> str:
    """The list of checks, or the explanation of one of them."""
    if name is not None:
        info = CHECKS[name]
        return (
            f"table-audit {version}: explain {name}\n\n"
            f"severity  {info.severity}\ncommands  {info.commands}\nlooks for {info.meaning}\n"
        )
    rows = [("check", "severity", "commands", "what it looks for")]
    rows += [(check, info.severity, info.commands, info.meaning) for check, info in CHECKS.items()]
    sizes = [max(len(r[i]) for r in rows) for i in range(3)]
    out = [f"table-audit {version}: explain", ""]
    for number, row in enumerate(rows):
        out.append("  " + "  ".join([*(row[i].ljust(sizes[i]) for i in range(3)), row[3]]))
        if number == 0:
            out.append("  " + "  ".join([*("-" * size for size in sizes), "-" * len(row[3])]))
    return "\n".join(out) + "\n"
