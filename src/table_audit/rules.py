"""Rules written down by a person: the columns a table must have and what may be in them.

A rules file is JSON, so that it can live next to the data in a repository and be reviewed like code:

    {
      "columns": {
        "subject_id": {"type": "text", "not_null": true, "pattern": "P\\\\d{4}"},
        "age": {"type": "int", "min": 18, "max": 90},
        "sex": {"allowed": ["F", "M"]},
        "visit_date": {"type": "date", "min": "2026-01-01"}
      },
      "unique": [["subject_id", "visit"]],
      "no_extra_columns": false
    }

A mistake in the rules file is reported with the place where it is ("columns.age: unknown key 'maximum'"), because
a validator that fails in an unclear way is not one anyone will trust.
"""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .profile import distinct_examples, duplicate_keys, first, not_of_the_type
from .reader import Table
from .report import MAX_EXAMPLES, Finding, counted
from .values import parse_date, parse_integer, parse_number, show_number

TYPES = ("int", "float", "number", "date", "bool", "text")
COLUMN_KEYS = ("type", "not_null", "unique", "min", "max", "allowed", "pattern")
TOP_KEYS = ("columns", "unique", "no_extra_columns")
TRUE_FALSE = ("true", "false", "0", "1")


class RulesError(Exception):
    """The rules file is not valid; the message says where."""


@dataclass(frozen=True)
class ColumnRule:
    name: str
    type: str | None = None
    not_null: bool = False
    unique: bool = False
    minimum: float | date | None = None
    maximum: float | date | None = None
    allowed: tuple[str, ...] | None = None
    pattern: str | None = None


@dataclass(frozen=True)
class Rules:
    columns: tuple[ColumnRule, ...] = ()
    unique: tuple[tuple[str, ...], ...] = ()
    no_extra_columns: bool = False


def _unknown(where: str, key: str, known: tuple[str, ...]) -> RulesError:
    close = difflib.get_close_matches(key, known, n=1)
    hint = f"; did you mean {close[0]!r}?" if close else f"; the keys are {', '.join(known)}"
    return RulesError(f"{where}: unknown key {key!r}{hint}")


def _flag(where: str, mapping: dict[str, object], key: str) -> bool:
    value = mapping.get(key, False)
    if not isinstance(value, bool):
        raise RulesError(f"{where}.{key}: must be true or false")
    return value


def _bound(where: str, key: str, value: object, kind: str | None) -> float | date:
    if kind == "date" or (kind is None and isinstance(value, str)):
        parsed = parse_date(value) if isinstance(value, str) else None
        if parsed is None:
            raise RulesError(f"{where}.{key}: a date is written 2026-03-02, not {value!r}")
        return parsed
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RulesError(f"{where}.{key}: must be a number, not {value!r}")
    return float(value)


def _column_rule(name: str, spec: object) -> ColumnRule:
    where = f"columns.{name}"
    if not isinstance(spec, dict):
        raise RulesError(f'{where}: must be an object such as {{"type": "int"}}')
    for key in spec:
        if key not in COLUMN_KEYS:
            raise _unknown(where, key, COLUMN_KEYS)
    kind = spec.get("type")
    if kind is not None and kind not in TYPES:
        raise RulesError(f"{where}.type: must be one of {', '.join(TYPES)}, not {kind!r}")
    if kind in ("text", "bool") and ("min" in spec or "max" in spec):
        raise RulesError(f"{where}: min and max need a type of int, float, number or date, not {kind!r}")
    allowed = spec.get("allowed")
    if allowed is not None:
        if not isinstance(allowed, list) or not all(
            isinstance(v, (str, int)) and not isinstance(v, bool) for v in allowed
        ):
            raise RulesError(f"{where}.allowed: must be a list of texts or whole numbers")
        allowed = tuple(str(v) for v in allowed)
    pattern = spec.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str):
            raise RulesError(f"{where}.pattern: must be a text")
        try:
            re.compile(pattern)
        except re.error as error:
            raise RulesError(f"{where}.pattern: not a valid regular expression ({error})") from None
    minimum = _bound(where, "min", spec["min"], kind) if "min" in spec else None
    maximum = _bound(where, "max", spec["max"], kind) if "max" in spec else None
    if minimum is not None and maximum is not None:
        if isinstance(minimum, date) != isinstance(maximum, date):
            raise RulesError(f"{where}: min and max must both be numbers or both be dates")
        if _less(maximum, minimum):
            raise RulesError(f"{where}: min is greater than max")
    return ColumnRule(
        name=name,
        type=kind,
        not_null=_flag(where, spec, "not_null"),
        unique=_flag(where, spec, "unique"),
        minimum=minimum,
        maximum=maximum,
        allowed=allowed,
        pattern=pattern,
    )


def parse_rules(data: object) -> Rules:
    if not isinstance(data, dict):
        raise RulesError("the rules must be a JSON object with a key 'columns' and, perhaps, 'unique'")
    for key in data:
        if key not in TOP_KEYS:
            raise _unknown("rules", key, TOP_KEYS)
    columns = data.get("columns", {})
    if not isinstance(columns, dict):
        raise RulesError("columns: must be an object with one entry per column")
    unique_raw = data.get("unique", [])
    if not isinstance(unique_raw, list) or not all(
        isinstance(u, list) and u and all(isinstance(c, str) for c in u) for u in unique_raw
    ):
        raise RulesError('unique: must be a list of lists of column names, such as [["subject_id", "visit"]]')
    rules = Rules(
        columns=tuple(_column_rule(str(name), spec) for name, spec in columns.items()),
        unique=tuple(tuple(u) for u in unique_raw),
        no_extra_columns=_flag("rules", data, "no_extra_columns"),
    )
    if not rules.columns and not rules.unique:
        raise RulesError("the rules say nothing: add 'columns' or 'unique'")
    return rules


def load_rules(path: Path) -> Rules:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise RulesError(f"cannot read {path}: {error.strerror or error}") from None
    try:
        return parse_rules(json.loads(text))
    except json.JSONDecodeError as error:
        raise RulesError(f"{path.name} is not valid JSON: {error}") from None
    except RulesError as error:
        raise RulesError(f"{path.name}: {error}") from None


def _fits(text: str, kind: str, decimal: str) -> bool:
    if kind == "int":
        return parse_integer(text) is not None
    if kind in ("float", "number"):
        return parse_number(text, decimal) is not None
    if kind == "date":
        return parse_date(text) is not None
    if kind == "bool":
        return text.strip().casefold() in TRUE_FALSE
    return True


def _less(a: float | date, b: float | date) -> bool:
    """a < b, for two numbers or two dates (a number is never compared with a date: the rules are checked for that)."""
    if isinstance(a, date) and isinstance(b, date):
        return a < b
    if isinstance(a, date) or isinstance(b, date):
        return False
    return a < b


def _value(text: str, rule: ColumnRule, decimal: str) -> float | date | None:
    """The cell as something that can be compared with min and max, or None when it cannot be."""
    if isinstance(rule.minimum, date) or isinstance(rule.maximum, date):
        return parse_date(text)
    return parse_number(text, decimal)


def check_column(table: Table, rule: ColumnRule) -> list[Finding]:
    position = table.index(rule.name)
    cells = table.present_at(position)
    found: list[Finding] = []
    name = rule.name

    if rule.not_null:
        gaps = [
            line
            for line, row in zip(table.lines, table.rows, strict=True)
            if row[position].strip().casefold() in table.missing
        ]
        if gaps:
            found.append(
                Finding(
                    "not-null",
                    "error",
                    f"{counted(len(gaps), 'cell is', 'cells are')} empty or missing",
                    name,
                    len(gaps),
                    first(gaps),
                )
            )

    if rule.type:
        wrong = [(line, text) for line, text in cells if not _fits(text, rule.type, table.decimal)]
        if wrong:
            found.append(not_of_the_type(table, name, rule.type, wrong, "wrong-type", "error"))
        cells = [(line, text) for line, text in cells if _fits(text, rule.type, table.decimal)]

    if rule.minimum is not None or rule.maximum is not None:
        below: list[tuple[int, str]] = []
        above: list[tuple[int, str]] = []
        for line, text in cells:
            value = _value(text, rule, table.decimal)
            if value is None:
                continue
            if rule.minimum is not None and _less(value, rule.minimum):
                below.append((line, text.strip()))
            elif rule.maximum is not None and _less(rule.maximum, value):
                above.append((line, text.strip()))
        if below or above:
            found.append(_range_finding(rule, below, above))

    if rule.allowed is not None:
        outside = [(line, text.strip()) for line, text in cells if text.strip() not in rule.allowed]
        if outside:
            common = [value for value, _ in Counter(v for _, v in outside).most_common(MAX_EXAMPLES)]
            listed = ", ".join(rule.allowed[:8]) + (", ..." if len(rule.allowed) > 8 else "")
            found.append(
                Finding(
                    "not-allowed",
                    "error",
                    f"{counted(len(outside), 'value is', 'values are')} not in the allowed list ({listed})",
                    name,
                    len(outside),
                    first([line for line, _ in outside]),
                    tuple(common),
                )
            )

    if rule.pattern is not None:
        pattern = re.compile(rule.pattern)
        bad = [(line, text.strip()) for line, text in cells if not pattern.fullmatch(text.strip())]
        if bad:
            found.append(
                Finding(
                    "pattern",
                    "error",
                    f"{counted(len(bad), 'value does', 'values do')} not match the pattern {rule.pattern}",
                    name,
                    len(bad),
                    first([line for line, _ in bad]),
                    distinct_examples([text for _, text in bad]),
                )
            )

    if rule.unique:
        found.extend(_repeated_values(name, cells))
    return found


def _range_finding(rule: ColumnRule, below: list[tuple[int, str]], above: list[tuple[int, str]]) -> Finding:
    def show(bound: float | date) -> str:
        return bound.isoformat() if isinstance(bound, date) else show_number(bound)

    outside = sorted(below + above)
    if rule.minimum is not None and rule.maximum is not None:
        limits = f"outside {show(rule.minimum)} to {show(rule.maximum)}"
        if below and above:
            limits += f" ({len(below)} below, {len(above)} above)"
    elif rule.minimum is not None:
        limits = f"below the minimum {show(rule.minimum)}"
    else:
        assert rule.maximum is not None
        limits = f"above the maximum {show(rule.maximum)}"
    return Finding(
        "out-of-range",
        "error",
        f"{counted(len(outside), 'value is', 'values are')} {limits}",
        rule.name,
        len(outside),
        first([line for line, _ in outside]),
        distinct_examples([text for _, text in outside]),
    )


def _repeated_values(name: str, cells: list[tuple[int, str]]) -> list[Finding]:
    seen: dict[str, int] = {}
    extra: list[int] = []
    repeated: dict[str, None] = {}
    for line, text in cells:
        value = text.strip()
        if value in seen:
            extra.append(line)
            repeated[value] = None
        else:
            seen[value] = line
    if not extra:
        return []
    return [
        Finding(
            "duplicate-key",
            "error",
            f"values must be unique: {counted(len(repeated), 'value appears', 'values appear')} more than once "
            f"({counted(len(extra), 'extra row', 'extra rows')})",
            name,
            len(extra),
            first(extra),
            tuple(list(repeated)[:MAX_EXAMPLES]),
        )
    ]


def apply_rules(table: Table, rules: Rules) -> list[Finding]:
    """Every way in which the table breaks the rules."""
    found: list[Finding] = []
    named = [rule.name for rule in rules.columns]
    for name in named:
        if not table.has(name):
            found.append(
                Finding("missing-column", "error", f"the rules ask for a column {name!r} that the table does not have")
            )
    if rules.no_extra_columns:
        extra = [c for c in table.columns if c not in named]
        if extra:
            found.append(Finding("extra-column", "error", f"columns that the rules do not mention: {', '.join(extra)}"))
    for rule in rules.columns:
        if table.has(rule.name):
            found.extend(check_column(table, rule))
    for key in rules.unique:
        absent = [c for c in key if not table.has(c)]
        if absent:
            found.append(
                Finding(
                    "missing-column",
                    "error",
                    f"the unique key ({', '.join(key)}) uses a column the table does not have: {', '.join(absent)}",
                )
            )
        else:
            found.extend(duplicate_keys(table, key))
    return found
