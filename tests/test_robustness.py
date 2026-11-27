"""Whatever the file is, the answer is a report or a message, never a traceback.

The inputs here are made by a seeded random generator, so a failure can be repeated: the message names the seed and
the bytes. They are meant to be nasty: ragged rows, quotes that never close, binary noise, a BOM in the middle,
numbers that overflow, a header with the same name three times.
"""

from __future__ import annotations

import json
import random

import pytest

from table_audit.leakage import Options, audit_leakage
from table_audit.profile import profile
from table_audit.reader import TableError, parse_table
from table_audit.report import Report, render_json, render_text
from table_audit.rules import apply_rules, parse_rules

CELLS = [
    "",
    " ",
    "0",
    "1",
    "-1",
    "007",
    "12",
    "1,5",
    "1.5",
    "2,25",
    "NA",
    "nd",
    "x",
    "X ",
    "Não",
    "nao",
    "2026-03-02",
    "02/03/2026",
    "2026-02-30",
    "true",
    "FALSE",
    "-999",
    "9999",
    "1e308",
    "1e999",
    "nan",
    "inf",
    "-",
    "?",
    "é",
    " ",
    '"quoted,cell"',
    '"two\nlines"',
    '"never closed',
    "a" * 300,
    "9" * 40,
]
DELIMITERS = [",", ";", "\t", "|"]
ENCODINGS = ["utf-8", "cp1252", "latin-1", "utf-8-sig"]


def random_file(rng: random.Random) -> bytes:
    delimiter = rng.choice(DELIMITERS)
    n_columns = rng.randint(1, 7)
    names = [rng.choice(["id", "x", "y", "date", "qc", "", " a ", "x"]) for _ in range(n_columns)]
    lines = [delimiter.join(names)]
    for _ in range(rng.randint(0, 60)):
        width = n_columns + rng.choice([0, 0, 0, 0, -1, 1, 3])
        lines.append(delimiter.join(rng.choice(CELLS) for _ in range(max(width, 0))))
        if rng.random() < 0.05:
            lines.append("")
    newline = rng.choice(["\n", "\r\n", "\r"])
    data = newline.join(lines).encode(rng.choice(ENCODINGS), errors="replace")
    roll = rng.random()
    if roll < 0.05:
        data = rng.randbytes(rng.randint(0, 200))  # binary noise
    elif roll < 0.10:
        data = data[: rng.randint(0, len(data))]  # cut in the middle of a row
    elif roll < 0.13:
        data = data.replace(b"\n", b"\x00\n")
    return data


def everything(report: Report) -> None:
    """The report can be printed in both formats, and the JSON is JSON."""
    text = render_text(report, "test")
    assert text.endswith("\n") and "result:" in text
    assert json.loads(render_json(report, "test"))["command"] == report.command


RULES = parse_rules(
    {
        "columns": {
            "id": {"not_null": True, "unique": True, "pattern": r"\w+"},
            "x": {"type": "float", "min": 0, "max": 100},
            "y": {"type": "int", "allowed": [0, 1]},
            "date": {"type": "date", "min": "2020-01-01"},
            "qc": {"type": "bool"},
        },
        "unique": [["id", "x"]],
    }
)


@pytest.mark.parametrize("seed", range(60))
def test_no_file_makes_the_checks_fail_with_anything_but_a_message(seed: int) -> None:
    rng = random.Random(seed)
    for case in range(25):
        data = random_file(rng)
        where = f"seed {seed}, case {case}, bytes {data[:300]!r}"
        try:
            table = parse_table(data, "fuzz.csv")
        except TableError:
            continue
        except Exception as error:  # noqa: BLE001 - the point of the test is to catch every kind
            pytest.fail(f"reading raised {error!r}: {where}")
        try:
            everything(profile(table, ()))
            keys = tuple(c for c in table.columns[:2] if c)
            if keys:
                everything(profile(table, keys))
            report = Report("check", ["fuzz.csv"])
            report.findings = apply_rules(table, RULES)
            everything(report)
        except TableError:
            continue  # a rules column the table does not have is a usage error, and says so
        except Exception as error:  # noqa: BLE001
            pytest.fail(f"auditing raised {error!r}: {where}")


@pytest.mark.parametrize("seed", range(40))
def test_no_pair_of_files_makes_the_leakage_checks_fail_with_anything_but_a_message(seed: int) -> None:
    rng = random.Random(1000 + seed)
    for case in range(15):
        first, second = random_file(rng), random_file(rng)
        where = f"seed {1000 + seed}, case {case}, bytes {first[:200]!r} / {second[:200]!r}"
        try:
            train, test = parse_table(first, "train.csv"), parse_table(second, "test.csv")
        except TableError:
            continue
        shared = [c for c in train.columns if c and c in test.columns]
        options = Options(
            groups=tuple(shared[:1]),
            target=rng.choice([None, *shared[:3]]) if shared else None,
            time=rng.choice([None, "date"]),
            ignore=tuple(shared[1:2]),
        )
        try:
            everything(audit_leakage(train, test, options))
        except TableError:
            continue
        except Exception as error:  # noqa: BLE001
            pytest.fail(f"leakage raised {error!r} with {options}: {where}")


def test_a_large_table_is_audited_in_a_reasonable_time() -> None:
    rng = random.Random(0)
    lines = ["id;x;y;sex;date"]
    for i in range(60_000):
        lines.append(
            f"{i};{rng.uniform(0, 100):.3f};{rng.randint(0, 5)};{rng.choice('FM')};2026-01-{rng.randint(1, 28):02d}"
        )
    table = parse_table("\n".join(lines).encode(), "big.csv", decimal=".")
    import time

    started = time.perf_counter()
    report = profile(table, ("id",))
    leak = audit_leakage(table, table, Options(("id",), "sex", "date"))
    elapsed = time.perf_counter() - started
    assert report.findings == [] or isinstance(report.findings, list)
    assert leak.findings  # the same table twice leaks everything
    assert elapsed < 20, f"{elapsed:.1f} s for 60,000 rows: something is quadratic"
