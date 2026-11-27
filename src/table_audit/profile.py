"""The checks that need no rules: what the table gives away about itself.

Each check is a function from a Table to findings. The thresholds are named constants at the top, because they are
judgements, and a judgement that is written down can be argued with.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from .reader import Table
from .report import MAX_EXAMPLES, ColumnProfile, Finding, Report, counted, plural
from .values import classify, date_style, fold, parse_date, parse_number, show_number

MIXED_SHARE = 0.9  # a column is "numbers" when at least this share of its cells are; the rest are reported
MANY_MISSING = 0.5  # a column with this share of missing cells (or more) is worth a warning
OUTLIER_Z = 6.0  # a value this many robust deviations from the median is far from the rest
MIN_FOR_OUTLIERS = 20  # fewer numbers than this and "far from the rest" means little
MIN_FOR_SENTINELS = 8
CATEGORY_MAX_DISTINCT = 50  # a text column with more distinct values than this is not a list of categories
SENTINELS = (-9999.0, -999.0, -99.0, 999.0, 9999.0, 99999.0)  # numbers that people use instead of "missing"
MAD_TO_SIGMA = 1.4826  # makes the median absolute deviation comparable to a standard deviation

# what a cell of each type is called, in the singular and in the plural
NOUNS = {
    "number": ("a number", "numbers"),
    "int": ("a whole number", "whole numbers"),
    "float": ("a number", "numbers"),
    "date": ("a date", "dates"),
    "bool": ("a true/false value", "true/false values"),
}


def delimiter_name(delimiter: str) -> str:
    return "tab" if delimiter == "\t" else f"'{delimiter}'"


def facts(table: Table) -> list[tuple[str, str]]:
    detected = " (detected)" if table.delimiter_detected else ""
    decimal = " (detected)" if table.decimal_detected else ""
    return [
        ("file", table.name),
        ("shape", f"{len(table.rows)} rows x {len(table.columns)} columns"),
        (
            "format",
            f"delimiter {delimiter_name(table.delimiter)}{detected}, decimal '{table.decimal}'{decimal}, "
            f"encoding {table.encoding}",
        ),
    ]


def first(lines: list[int]) -> tuple[int, ...]:
    return tuple(lines[:MAX_EXAMPLES])


def distinct_examples(values: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
        if len(seen) == MAX_EXAMPLES:
            break
    return tuple(seen)


def audit_structure(table: Table) -> list[Finding]:
    """The shape of the file: names of the columns, rows with the wrong number of cells, empty rows, encoding."""
    found = [Finding("encoding", "info", note) for note in table.notes]
    for position, name in enumerate(table.columns, start=1):
        if not name:
            found.append(Finding("column-name", "error", f"column {position} has no name"))
    counts = Counter(table.columns)
    for name, times in counts.items():
        if name and times > 1:
            places = [str(i) for i, c in enumerate(table.columns, start=1) if c == name]
            found.append(
                Finding("column-name", "error", f"the name {name!r} is used by {times} columns ({', '.join(places)})")
            )
    if table.ragged:
        lines = [line for line, _ in table.ragged]
        found.append(
            Finding(
                "ragged-rows",
                "error",
                f"{counted(len(lines), 'row does', 'rows do')} not have the {len(table.columns)} cells of the header "
                "(padded or cut to fit)",
                count=len(lines),
                lines=first(lines),
            )
        )
    if table.blank_lines:
        found.append(
            Finding(
                "blank-lines",
                "info",
                f"{counted(table.blank_lines, 'blank line was', 'blank lines were')} skipped",
                count=table.blank_lines,
            )
        )
    empty = [line for line, row in zip(table.lines, table.rows, strict=True) if all(_is_missing(table, c) for c in row)]
    if empty:
        found.append(
            Finding(
                "empty-rows",
                "warning",
                f"{counted(len(empty), 'row has', 'rows have')} no value at all",
                count=len(empty),
                lines=first(empty),
            )
        )
    if not table.rows:
        found.append(Finding("no-rows", "warning", "the file has a header and no rows"))
    return found


def _is_missing(table: Table, text: str) -> bool:
    return text.strip().casefold() in table.missing


def audit_columns(table: Table) -> tuple[list[ColumnProfile], list[Finding]]:
    """Kind, missing values, mixed types, spaces, spelling variants, placeholders and outliers, column by column."""
    profiles: list[ColumnProfile] = []
    findings: list[Finding] = []
    n_rows = len(table.rows)
    for position, name in enumerate(table.columns):
        if not name or table.columns.index(name) != position:
            continue  # unnamed and repeated columns are reported by audit_structure; look at each name once
        profile, column_findings = _audit_column(table, position, name, n_rows)
        profiles.append(profile)
        findings.extend(column_findings)
    return profiles, findings


def _audit_column(table: Table, position: int, name: str, n_rows: int) -> tuple[ColumnProfile, list[Finding]]:
    cells = table.present_at(position)
    n_missing = n_rows - len(cells)
    found: list[Finding] = []
    if not cells:
        if n_rows:
            found.append(Finding("empty-column", "warning", "no row has a value", column=name))
        return ColumnProfile(name, "empty", n_missing, 0), found

    classes = [classify(text, table.decimal) for _, text in cells]
    tally = Counter(classes)
    groups = {
        "number": tally["int"] + tally["float"],
        "date": tally["date"],
        "bool": tally["bool"],
        "text": tally["text"],
    }
    total = len(cells)
    majority = max(groups, key=lambda g: (groups[g], -list(groups).index(g)))
    share = groups[majority] / total

    if groups["text"] / total >= 0.5:
        kind = "text"
    elif share < MIXED_SHARE:
        kind = "mixed"
        parts = ", ".join(f"{round(100 * n / total)}% {g}" for g, n in groups.items() if n / total >= 0.05)
        found.append(Finding("mixed-types", "warning", f"no clear type: {parts}", column=name))
    elif majority == "number":
        kind = "int" if tally["float"] == 0 else "float"
    else:
        kind = majority

    if kind in ("int", "float", "date", "bool") and share < 1:
        wrong = [(line, text) for (line, text), c in zip(cells, classes, strict=True) if _group(c) != majority]
        found.append(not_of_the_type(table, name, majority, wrong))

    present_values = [text.strip() for _, text in cells]
    distinct = len(set(present_values))
    minimum = maximum = ""
    if kind in ("int", "float"):
        numbers = [v for _, text in cells if (v := parse_number(text, table.decimal)) is not None]
        minimum, maximum = show_number(min(numbers)), show_number(max(numbers))
        found.extend(_numeric_findings(name, cells, table.decimal))
    elif kind == "date":
        days = [d for _, text in cells if (d := parse_date(text)) is not None]
        minimum, maximum = min(days).isoformat(), max(days).isoformat()
        found.extend(_date_styles(name, cells))

    if n_rows and n_missing / n_rows >= MANY_MISSING:
        found.append(
            Finding(
                "missing",
                "warning",
                f"no value in {n_missing} of {n_rows} rows ({round(100 * n_missing / n_rows)}%)",
                column=name,
            )
        )
    if distinct == 1 and n_missing == 0 and n_rows > 1:
        found.append(Finding("constant", "info", f"every row has the same value, '{present_values[0]}'", column=name))

    spaced = [(line, text) for line, text in cells if text != text.strip()]
    if spaced:
        found.append(
            Finding(
                "whitespace",
                "warning",
                f"{counted(len(spaced), 'value has', 'values have')} spaces at the start or end "
                "(a join will miss them)",
                column=name,
                count=len(spaced),
                lines=first([line for line, _ in spaced]),
                examples=distinct_examples([text for _, text in spaced]),
            )
        )
    if kind == "text" and distinct <= CATEGORY_MAX_DISTINCT:
        found.extend(_variants(name, cells))

    return ColumnProfile(name, kind, n_missing, distinct, minimum, maximum), found


def _group(kind: str) -> str:
    return "number" if kind in ("int", "float") else kind


def not_of_the_type(
    table: Table,
    name: str,
    wanted: str,
    wrong: list[tuple[int, str]],
    check: str = "mixed-types",
    severity: str = "warning",
) -> Finding:
    """The cells of a column that are not of the type the rest has (or the rules ask for)."""
    one, many = NOUNS[wanted]
    message = f"{counted(len(wrong), f'value is not {one}', f'values are not {many}')}"
    if wanted in ("number", "int", "float"):
        other = "." if table.decimal == "," else ","
        swapped = sum(1 for _, text in wrong if parse_number(text, other) is not None)
        if swapped:
            how = ("all " if len(wrong) > 1 else "") if swapped == len(wrong) else f"{swapped} "
            message += f" ({how}written with '{other}' instead of '{table.decimal}')"
    return Finding(
        check,
        severity,
        message,
        column=name,
        count=len(wrong),
        lines=first([line for line, _ in wrong]),
        examples=distinct_examples([text.strip() for _, text in wrong]),
    )


def _numeric_findings(name: str, cells: list[tuple[int, str]], decimal: str) -> list[Finding]:
    """Placeholders for "missing" (-999, 9999...) and values far from the rest, in a column of numbers."""
    pairs = [(line, v) for line, text in cells if (v := parse_number(text, decimal)) is not None]
    if len(pairs) < MIN_FOR_SENTINELS:
        return []
    values = [v for _, v in pairs]
    median = statistics.median(values)
    mad = statistics.median(abs(v - median) for v in values)
    scale = MAD_TO_SIGMA * mad

    def far(v: float) -> bool:
        return abs(v - median) > OUTLIER_Z * scale if scale > 0 else v != median

    found: list[Finding] = []
    placeholders = [(line, v) for line, v in pairs if v in SENTINELS and far(v)]
    if placeholders:
        found.append(
            Finding(
                "placeholder",
                "warning",
                f"{counted(len(placeholders), 'value looks', 'values look')} like a code for 'missing', "
                "not a measurement",
                column=name,
                count=len(placeholders),
                lines=first([line for line, _ in placeholders]),
                examples=distinct_examples([show_number(v) for _, v in placeholders]),
            )
        )
    if scale > 0 and len(pairs) >= MIN_FOR_OUTLIERS:
        taken = {line for line, _ in placeholders}
        outliers = sorted(
            ((abs(v - median) / scale, line, v) for line, v in pairs if far(v) and line not in taken), reverse=True
        )
        if outliers:
            found.append(
                Finding(
                    "outliers",
                    "info",
                    f"{counted(len(outliers), 'value is', 'values are')} more than {OUTLIER_Z:g} robust deviations "
                    "from the median",
                    column=name,
                    count=len(outliers),
                    lines=first([line for _, line, _ in outliers]),
                    examples=distinct_examples([show_number(v) for _, _, v in outliers]),
                )
            )
    return found


def _date_styles(name: str, cells: list[tuple[int, str]]) -> list[Finding]:
    """A column of dates written two ways (2026-03-02 and 02/03/2026) is usually two files pasted together."""
    by_style: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for line, text in cells:
        style = date_style(text)
        if style:
            by_style[style].append((line, text.strip()))
    if len(by_style) < 2:
        return []
    iso, day_first = by_style["iso"], by_style["day-first"]
    minority = min(iso, day_first, key=len)
    return [
        Finding(
            "date-formats",
            "warning",
            f"dates are written in two ways: {len(iso)} like 2026-03-02 and {len(day_first)} like 02/03/2026",
            column=name,
            count=len(minority),
            lines=first([line for line, _ in minority]),
            examples=distinct_examples([text for _, text in minority]),
        )
    ]


def _variants(name: str, cells: list[tuple[int, str]]) -> list[Finding]:
    """Labels that differ only in case, accents or spaces ('Feminino' and 'feminino')."""
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for _, text in cells:
        spellings[fold(text)][text.strip()] += 1
    split = {key: counter for key, counter in spellings.items() if len(counter) > 1}
    if not split:
        return []
    minority: set[str] = set()
    for counter in split.values():
        minority.update(label for label, _ in counter.most_common()[1:])
    lines = [line for line, text in cells if text.strip() in minority]
    examples = [" / ".join(label for label, _ in counter.most_common()) for counter in split.values()]
    return [
        Finding(
            "label-variants",
            "warning",
            f"{counted(len(split), 'label is', 'labels are')} written in more than one way (case, accents or spaces)",
            column=name,
            count=len(lines),
            lines=first(lines),
            examples=tuple(examples[:MAX_EXAMPLES]),
        )
    ]


def audit_duplicates(table: Table, keys: tuple[str, ...] = ()) -> list[Finding]:
    """Rows that repeat an earlier row, and (when the key is known) keys that are not unique."""
    found: list[Finding] = []
    seen: dict[tuple[str, ...], int] = {}
    repeats: list[int] = []
    for line, row in zip(table.lines, table.rows, strict=True):
        if all(_is_missing(table, cell) for cell in row):
            continue  # an empty row is not a repeated one; audit_structure reports it
        signature = tuple(cell.strip() for cell in row)
        if signature in seen:
            repeats.append(line)
        else:
            seen[signature] = line
    if repeats:
        found.append(
            Finding(
                "duplicate-rows",
                "warning",
                f"{counted(len(repeats), 'row repeats', 'rows repeat')} an earlier row exactly "
                "(spaces at the ends ignored)",
                count=len(repeats),
                lines=first(repeats),
            )
        )
    if keys:
        found.extend(duplicate_keys(table, keys))
    return found


def duplicate_keys(table: Table, keys: tuple[str, ...]) -> list[Finding]:
    positions = [table.index(k) for k in keys]
    first_line: dict[tuple[str, ...], int] = {}
    extra: list[int] = []
    repeated: dict[tuple[str, ...], None] = {}
    for line, row in zip(table.lines, table.rows, strict=True):
        key = tuple(row[p].strip() for p in positions)
        if key in first_line:
            extra.append(line)
            repeated[key] = None
        else:
            first_line[key] = line
    if not extra:
        return []
    return [
        Finding(
            "duplicate-key",
            "error",
            f"({', '.join(keys)}) is not unique: {counted(len(repeated), 'key appears', 'keys appear')} more than "
            f"once ({plural(len(extra), 'extra row')})",
            count=len(extra),
            lines=first(extra),
            examples=tuple("/".join(k) for k in list(repeated)[:MAX_EXAMPLES]),
        )
    ]


def profile(table: Table, keys: tuple[str, ...] = ()) -> Report:
    """The whole profile of a table: its columns, and everything that looks wrong without any rules."""
    columns, column_findings = audit_columns(table)
    report = Report("profile", [table.name], facts(table), columns)
    report.findings = [*audit_structure(table), *column_findings, *audit_duplicates(table, keys)]
    return report
