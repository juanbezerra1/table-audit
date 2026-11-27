"""Leakage between a training table and a test table.

A model that is tested on rows it has, in effect, already seen reports a score that it will not have in use. The ways
in which that happens in tables of measurements are few and old: the same person is on both sides of the split, the
same row is, a column carries the answer, or the test is older than the training data. This module looks for those
four. It cannot prove that a split is clean, only find the commonest ways in which it is not.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from .profile import first
from .reader import Table, TableError
from .report import MAX_EXAMPLES, Finding, Report, counted
from .values import parse_date, parse_number

MIN_ROWS = 20  # the checks on the target say little about fewer rows than this
MAX_CLASSES = 20  # a target with more distinct values than this is a quantity, not a label
PERFECT_AUC = 0.999
PERFECT_CORRELATION = 0.999


@dataclass(frozen=True)
class Options:
    groups: tuple[str, ...] = ()  # the columns that say who or what a row is about (subject, patient, device)
    target: str | None = None  # the column the model must predict
    time: str | None = None  # a date column, for splits that must respect the order of time
    ignore: tuple[str, ...] = ()  # columns left out of the comparison of rows


def auc(scores: list[float], labels: list[bool]) -> float | None:
    """The area under the ROC curve of `scores` for `labels` (ties share their rank), or None if one class is empty."""
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return None
    order = sorted(range(len(scores)), key=scores.__getitem__)
    ranks = [0.0] * len(scores)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and scores[order[end + 1]] == scores[order[start]]:
            end += 1
        for k in range(start, end + 1):
            ranks[order[k]] = (start + end) / 2 + 1
        start = end + 1
    rank_sum = sum(rank for rank, label in zip(ranks, labels, strict=True) if label)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def pearson(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 2:
        return None
    mean_x, mean_y = sum(x) / n, sum(y) / n
    sxx = sum((a - mean_x) ** 2 for a in x)
    syy = sum((b - mean_y) ** 2 for b in y)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True)) / math.sqrt(sxx * syy)


def _need(table: Table, column: str, option: str) -> None:
    if not table.has(column):
        raise TableError(
            f"{option} {column!r} is not a column of {table.name}; its columns are: {', '.join(table.columns)}"
        )


def group_overlap(train: Table, test: Table, groups: tuple[str, ...]) -> list[Finding]:
    """Subjects (or patients, or devices) that are on both sides of the split."""
    train_at = [train.index(g) for g in groups]
    test_at = [test.index(g) for g in groups]

    def key(row: list[str], at: list[int]) -> tuple[str, ...] | None:
        parts = tuple(row[p].strip() for p in at)
        return None if any(not p or p.casefold() in train.missing for p in parts) else parts

    seen = {k for row in train.rows if (k := key(row, train_at)) is not None}
    test_keys: dict[tuple[str, ...], int] = {}
    lines: list[int] = []
    for line, row in zip(test.lines, test.rows, strict=True):
        k = key(row, test_at)
        if k is None:
            continue
        test_keys.setdefault(k, line)
        if k in seen:
            lines.append(line)
    shared = [k for k in test_keys if k in seen]
    if not shared:
        return []
    label = "+".join(groups)
    return [
        Finding(
            "group-overlap",
            "error",
            f"{len(shared)} of {len(test_keys)} {label} values in the test file are also in the training file "
            f"({len(lines)} of {len(test.rows)} test rows)",
            count=len(lines),
            lines=first(lines),
            examples=tuple("/".join(k) for k in shared[:MAX_EXAMPLES]),
        )
    ]


def _canonical(cell: str, decimal: str) -> str:
    """A cell as it is compared between files: 1,50 and 1.5 are the same number, 'a ' and 'a' the same text."""
    number = parse_number(cell, decimal)
    return f"#{number!r}" if number is not None else cell.strip()


def identical_rows(train: Table, test: Table, options: Options) -> list[Finding]:
    """Test rows that have, in every shared column that is compared, the same values as a training row."""
    excluded = {*options.ignore, *([options.target] if options.target else [])}
    compared = [c for c in train.columns if c and c in test.columns and c not in excluded]
    if not compared:
        return []
    train_at = [train.index(c) for c in compared]
    test_at = [test.index(c) for c in compared]
    labels: dict[tuple[str, ...], set[str]] = defaultdict(set)
    target_train = train.index(options.target) if options.target else None
    for row in train.rows:
        signature = tuple(_canonical(row[p], train.decimal) for p in train_at)
        labels[signature].add(row[target_train].strip() if target_train is not None else "")
    target_test = test.index(options.target) if options.target and test.has(options.target) else None
    lines: list[int] = []
    conflicting = 0
    for line, row in zip(test.lines, test.rows, strict=True):
        signature = tuple(_canonical(row[p], test.decimal) for p in test_at)
        if signature in labels:
            lines.append(line)
            if target_test is not None and row[target_test].strip() not in labels[signature]:
                conflicting += 1
    if not lines:
        return []
    columns = "the only column compared" if len(compared) == 1 else f"all {len(compared)} columns compared"
    message = f"{counted(len(lines), 'test row has', 'test rows have')} the same values as a training row in {columns}"
    if target_test is not None and conflicting:
        message += f" ({conflicting} with a different {options.target})"
    return [Finding("identical-rows", "error", message, count=len(lines), lines=first(lines))]


def target_checks(train: Table, options: Options) -> list[Finding]:
    """Columns that carry the answer: each value goes with one class, a threshold splits two classes, or a copy."""
    assert options.target is not None
    target_at = train.index(options.target)
    skipped = {options.target, *options.groups, *options.ignore, *([options.time] if options.time else [])}
    target_values = [(i, row[target_at].strip()) for i, row in enumerate(train.rows)]
    target_values = [(i, v) for i, v in target_values if v and v.casefold() not in train.missing]
    has_target = {i for i, _ in target_values}
    if len(target_values) < MIN_ROWS:
        return [
            Finding(
                "target",
                "info",
                f"only {len(target_values)} rows have a {options.target}: the checks on it were skipped",
            )
        ]
    numbers = {i: n for i, v in target_values if (n := parse_number(v, train.decimal)) is not None}
    classes = sorted({v for _, v in target_values})
    quantity = len(numbers) >= 0.9 * len(target_values) and len(classes) > MAX_CLASSES
    if not quantity and len(classes) > MAX_CLASSES:
        return [
            Finding(
                "target",
                "info",
                f"{options.target} has {len(classes)} distinct values and is not numeric: its checks were skipped",
            )
        ]

    found: list[Finding] = []
    for position, name in enumerate(train.columns):
        if not name or name in skipped or train.columns.index(name) != position:
            continue
        pairs = [(row[position].strip(), train.rows[i][target_at].strip(), i) for i, row in enumerate(train.rows)]
        pairs = [(f, t, i) for f, t, i in pairs if f and f.casefold() not in train.missing and i in has_target]
        if len(pairs) < MIN_ROWS:
            continue
        values = [f for f, _, _ in pairs]
        numeric = [parse_number(f, train.decimal) for f in values]
        is_numeric = sum(n is not None for n in numeric) >= 0.9 * len(values)
        if quantity:
            xs = [
                (n, numbers[i]) for n, (_, _, i) in zip(numeric, pairs, strict=True) if n is not None and i in numbers
            ]
            r = pearson([a for a, _ in xs], [b for _, b in xs]) if is_numeric else None
            if r is not None and abs(r) >= PERFECT_CORRELATION:
                found.append(
                    Finding(
                        "target-copy", "warning", f"it is almost a copy of {options.target} (r = {r:.4f})", column=name
                    )
                )
            continue
        found.extend(_class_findings(name, pairs, numeric, is_numeric, classes, options.target))
    return found


def _class_findings(
    name: str,
    pairs: list[tuple[str, str, int]],
    numeric: list[float | None],
    is_numeric: bool,
    classes: list[str],
    target: str,
) -> list[Finding]:
    values = [f for f, _, _ in pairs]
    distinct = set(values)
    is_date = sum(parse_date(f) is not None for f in values) >= 0.9 * len(values)  # a date is not an identifier
    if not is_numeric and not is_date and len(distinct) == len(values):
        return [
            Finding(
                "id-like-column",
                "warning",
                "every value is different (an identifier?): a model can memorize it instead of learning from it",
                column=name,
            )
        ]
    if 2 <= len(distinct) <= len(values) / 2:
        by_value: dict[str, set[str]] = defaultdict(set)
        for f, t, _ in pairs:
            by_value[f].add(t)
        if all(len(t) == 1 for t in by_value.values()) and len({next(iter(t)) for t in by_value.values()}) >= 2:
            return [
                Finding(
                    "target-determined",
                    "warning",
                    f"each of its {len(distinct)} values goes with a single {target} in the training file; "
                    "it may be derived from the target or stand in for it",
                    column=name,
                )
            ]
    if is_numeric and len(classes) == 2:
        pairs_numeric = [(n, t) for n, (_, t, _) in zip(numeric, pairs, strict=True) if n is not None]
        score = auc([n for n, _ in pairs_numeric], [t == classes[1] for _, t in pairs_numeric])
        if score is not None and max(score, 1 - score) >= PERFECT_AUC:
            return [
                Finding(
                    "target-separated",
                    "warning",
                    f"one threshold on it separates the two classes of {target} in the training file "
                    f"(AUC {max(score, 1 - score):.3f})",
                    column=name,
                )
            ]
    return []


def time_order(train: Table, test: Table, column: str) -> list[Finding]:
    """With a split that must respect time, the test has to come after the training."""

    def days(table: Table) -> list[tuple[int, date]]:
        position = table.index(column)
        parsed = [(line, parse_date(row[position])) for line, row in zip(table.lines, table.rows, strict=True)]
        good = [(line, d) for line, d in parsed if d is not None]
        if not good or len(good) < 0.5 * len(table.rows):
            raise TableError(f"--time {column!r} of {table.name} does not hold dates (write 2026-03-02 or 02/03/2026)")
        return good

    train_days, test_days = days(train), days(test)
    latest = max(d for _, d in train_days)
    earliest = min(d for _, d in test_days)
    early = [line for line, d in test_days if d < latest]
    if not early:
        return []
    share = round(100 * len(early) / len(test.rows))
    return [
        Finding(
            "time-order",
            "warning",
            f"{counted(len(early), 'test row is', 'test rows are')} dated before the last training date, {latest} "
            f"({share}% of the test file; the first test date is {earliest})",
            count=len(early),
            lines=first(early),
        )
    ]


def audit_leakage(train: Table, test: Table, options: Options) -> Report:
    for table in (train, test):
        for group in options.groups:
            _need(table, group, "--group")
        if options.time:
            _need(table, options.time, "--time")
    if options.target:
        _need(train, options.target, "--target")
    for column in options.ignore:
        if not (train.has(column) or test.has(column)):
            raise TableError(f"--ignore {column!r} is not a column of either file")

    report = Report("leakage", [train.name, test.name])
    report.facts = [
        ("train", f"{train.name}: {len(train.rows)} rows x {len(train.columns)} columns"),
        ("test", f"{test.name}: {len(test.rows)} rows x {len(test.columns)} columns"),
    ]
    only_train = [c for c in train.columns if c not in test.columns]
    only_test = [c for c in test.columns if c not in train.columns]
    if only_train or only_test:
        report.findings.append(
            Finding(
                "columns-differ",
                "info",
                f"only in {train.name}: {', '.join(only_train) or 'none'}; "
                f"only in {test.name}: {', '.join(only_test) or 'none'}",
            )
        )
    if options.groups:
        report.findings += group_overlap(train, test, options.groups)
    report.findings += identical_rows(train, test, options)
    if options.target:
        report.findings += target_checks(train, options)
    if options.time:
        report.findings += time_order(train, test, options.time)
    return report


__all__ = ["Options", "audit_leakage", "auc", "pearson"]
