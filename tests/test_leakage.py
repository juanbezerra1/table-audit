"""Leakage between a training table and a test table: the four ways it commonly happens, and the clean split."""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any

import pytest

from table_audit.leakage import MAX_CLASSES, MIN_ROWS, Options, auc, audit_leakage, pearson
from table_audit.reader import Table, TableError, read_table

from .helpers import EXAMPLES, make, named, one

Row = dict[str, Any]
COLUMNS = ["subject_id", "date", "rms_uv", "site", "artifact_score", "qc"]


def csv_text(rows: list[Row], columns: list[str] | None = None) -> str:
    columns = columns or list(rows[0])
    return "\n".join([",".join(columns), *(",".join(str(r[c]) for c in columns) for r in rows)]) + "\n"


def make_rows(subjects: list[str], per_subject: int, first_day: date, rng: random.Random) -> list[Row]:
    rows: list[Row] = []
    for subject in subjects:
        for _ in range(per_subject):
            rows.append(
                {
                    "subject_id": subject,
                    "date": (first_day + timedelta(days=len(rows))).isoformat(),
                    "rms_uv": f"{rng.uniform(10, 50):.3f}",
                    "site": rng.choice("ABC"),
                    "artifact_score": f"{rng.uniform(0, 1):.3f}",
                    "qc": rng.choice(["ok", "bad"]),
                }
            )
    return rows


def healthy(seed: int = 1) -> tuple[list[Row], list[Row]]:
    """Ten subjects in training, four other ones in test, test after training, nothing that gives the target away."""
    rng = random.Random(seed)
    train = make_rows([f"S{i:02d}" for i in range(1, 11)], 4, date(2026, 1, 1), rng)
    test = make_rows([f"S{i:02d}" for i in range(11, 15)], 3, date(2026, 3, 1), rng)
    return train, test


def audit(train: list[Row], test: list[Row], **options: Any) -> list[Any]:
    defaults: dict[str, Any] = {"groups": ("subject_id",), "target": "qc", "time": "date"}
    report = audit_leakage(
        make(csv_text(train), "train.csv"), make(csv_text(test), "test.csv"), Options(**{**defaults, **options})
    )
    return report.findings


# --- the two small numerical helpers -----------------------------------------------------------------------------


def test_auc_of_a_perfect_a_reversed_and_a_useless_score() -> None:
    assert auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert auc([1, 2, 3, 4], [True, True, False, False]) == 0.0
    assert auc([1, 1, 1, 1], [True, False, True, False]) == 0.5  # ties share their rank
    assert auc([1, 2, 3, 4], [False, True, False, True]) == 0.75
    assert auc([1, 2, 3], [True, True, True]) is None  # one class only
    assert auc([], []) is None


def test_pearson_of_a_line_a_reversed_line_and_a_constant() -> None:
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)
    assert pearson([1, 2, 3], [5, 5, 5]) is None
    assert pearson([1], [1]) is None


# --- a clean split ------------------------------------------------------------------------------------------------


def test_a_clean_split_has_no_findings() -> None:
    for seed in range(1, 6):
        assert audit(*healthy(seed)) == [], f"seed {seed}"


def test_the_columns_that_are_in_one_file_only_are_information() -> None:
    train, test = healthy()
    for row in test:
        del row["site"]
    found = audit(train, test)
    assert [(f.check, f.severity) for f in found] == [("columns-differ", "info")]
    assert found[0].message == "only in train.csv: site; only in test.csv: none"


# --- the same people on both sides -------------------------------------------------------------------------------


def test_a_subject_in_both_files_is_an_error_with_the_numbers() -> None:
    train, test = healthy()
    test[0]["subject_id"] = "S03"
    test[5]["subject_id"] = "S03"
    finding = one(audit(train, test), "group-overlap")
    assert finding.severity == "error" and finding.count == 2 and finding.lines == (2, 7)
    assert (
        finding.message == "1 of 5 subject_id values in the test file are also in the training file (2 of 12 test rows)"
    )
    assert finding.examples == ("S03",)


def test_groups_can_be_several_columns_that_say_who_a_row_is_about() -> None:
    train = make("subject,hand,x\nS1,L,1\nS1,R,2\n", "train.csv")
    test = make("subject,hand,x\nS1,R,3\nS2,L,4\n", "test.csv")
    found = audit_leakage(train, test, Options(groups=("subject", "hand"))).findings
    assert one(found, "group-overlap").examples == ("S1/R",)
    only_subject = audit_leakage(train, test, Options(groups=("subject",))).findings
    assert one(only_subject, "group-overlap").examples == ("S1",)


def test_a_cell_with_no_group_is_not_a_group() -> None:
    train = make("subject,x\nS1,1\n,2\nNA,3\n", "train.csv")
    test = make("subject,x\n,4\nNA,5\nS9,6\n", "test.csv")
    assert named(audit_leakage(train, test, Options(groups=("subject",))).findings, "group-overlap") == []


# --- the same rows on both sides ---------------------------------------------------------------------------------


def test_a_test_row_that_is_a_training_row_is_an_error_even_when_the_numbers_are_written_differently() -> None:
    train = make("subject;x;y\nA;1,50;u\nB;2,00;v\n", "train.csv")  # decimal comma
    test = make("subject,x,y\nA,1.5,u\nC,3.0,w\n", "test.csv")  # decimal point
    found = audit_leakage(train, test, Options()).findings
    finding = one(found, "identical-rows")
    assert finding.lines == (2,) and finding.message == (
        "1 test row has the same values as a training row in all 3 columns compared"
    )


def test_the_target_is_not_compared_but_a_different_target_is_pointed_out() -> None:
    train = make("a,b,qc\n1,x,ok\n2,y,bad\n", "train.csv")
    test = make("a,b,qc\n1,x,bad\n2,y,bad\n", "test.csv")
    finding = one(audit_leakage(train, test, Options(target="qc")).findings, "identical-rows")
    assert (
        finding.message
        == "2 test rows have the same values as a training row in all 2 columns compared (1 with a different qc)"
    )


def test_columns_that_differ_on_purpose_can_be_left_out_of_the_comparison() -> None:
    train = make("a,when,qc\n1,2026-01-01,ok\n", "train.csv")
    test = make("a,when,qc\n1,2026-06-01,ok\n", "test.csv")
    assert named(audit_leakage(train, test, Options()).findings, "identical-rows") == []  # "when" differs
    assert named(audit_leakage(train, test, Options(target="qc", ignore=("when",))).findings, "identical-rows")
    only = make("a\n1\n", "train.csv")
    assert one(audit_leakage(only, make("a\n1\n", "test.csv"), Options()).findings, "identical-rows").message == (
        "1 test row has the same values as a training row in the only column compared"
    )


def test_rows_that_have_nothing_in_common_but_the_labels_are_not_the_same_row() -> None:
    train, test = healthy()
    assert named(audit(train, test), "identical-rows") == []


# --- a column that carries the answer ----------------------------------------------------------------------------


def test_a_column_whose_every_value_goes_with_one_class_is_a_warning() -> None:
    train, test = healthy()
    for row in train:
        row["site"] = "A" if row["qc"] == "ok" else "B"
    finding = one(audit(train, test), "target-determined", "site")
    assert finding.severity == "warning"
    assert finding.message == (
        "each of its 2 values goes with a single qc in the training file; it may be derived from the target or "
        "stand in for it"
    )


def test_a_threshold_that_separates_the_two_classes_is_a_warning_in_either_direction() -> None:
    for high in ("bad", "ok"):
        train, test = healthy()
        rng = random.Random(7)
        for row in train:
            row["artifact_score"] = f"{(0.8 if row['qc'] == high else 0.2) + rng.uniform(0, 0.1):.3f}"
        finding = one(audit(train, test), "target-separated", "artifact_score")
        assert finding.message.endswith("(AUC 1.000)"), finding.message


def test_a_score_that_only_leans_toward_a_class_is_not_a_leak() -> None:
    train, test = healthy()
    rng = random.Random(3)
    for row in train:
        row["artifact_score"] = f"{(0.55 if row['qc'] == 'bad' else 0.45) + rng.uniform(-0.3, 0.3):.3f}"
    assert named(audit(train, test), "target-separated") == []


def test_a_column_where_every_value_is_different_is_an_identifier_a_model_can_memorize() -> None:
    train, test = healthy()
    for number, row in enumerate(train):
        row["recording"] = f"rec-{number:03d}"
    for number, row in enumerate(test):
        row["recording"] = f"rec-{number + 100:03d}"
    finding = one(audit(train, test), "id-like-column", "recording")
    assert finding.message.startswith("every value is different (an identifier?)")


def test_numbers_and_dates_that_are_all_different_are_not_identifiers() -> None:
    train, test = healthy()
    assert len({r["date"] for r in train}) == len(train)  # one row a day: every date is different
    assert len({r["rms_uv"] for r in train}) == len(train)
    assert named(audit(train, test), "id-like-column") == []
    assert named(audit(train, test, time=None), "id-like-column") == []


def test_a_column_that_copies_a_numeric_target_is_a_warning() -> None:
    rng = random.Random(5)
    train = [{"subject_id": f"S{i:02d}", "force": f"{rng.uniform(10, 90):.2f}"} for i in range(40)]
    for row in train:
        row["force_kgf"] = f"{float(row['force']) / 9.80665:.4f}"
        row["noise"] = f"{rng.uniform(0, 1):.3f}"
    test = [{"subject_id": f"T{i:02d}", "force": "50", "force_kgf": "5.1", "noise": "0.5"} for i in range(5)]
    found = audit(train, test, target="force", time=None)
    finding = one(found, "target-copy", "force_kgf")
    assert finding.message.startswith("it is almost a copy of force (r = 1.000")
    assert named(found, "target-copy", "noise") == []


def test_few_rows_or_a_target_that_is_neither_a_label_nor_a_quantity_skip_the_checks_on_it() -> None:
    train, test = healthy()
    few = one(audit(train[: MIN_ROWS - 1], test), "target")
    assert (
        few.severity == "info" and few.message == f"only {MIN_ROWS - 1} rows have a qc: the checks on it were skipped"
    )
    for number, row in enumerate(train):
        row["qc"] = f"label-{number}"
    assert one(audit(train, test), "target").message == (
        f"qc has {len(train)} distinct values and is not numeric: its checks were skipped"
    )
    assert len(train) > MAX_CLASSES


# --- the test is older than the training ------------------------------------------------------------------------


def test_a_test_dated_before_the_end_of_the_training_is_a_warning() -> None:
    train, test = healthy()
    test[0]["date"] = "02/01/2026"  # day first
    test[1]["date"] = "2026-02-05"
    finding = one(audit(train, test), "time-order")
    assert finding.severity == "warning" and finding.count == 2 and finding.lines == (2, 3)
    assert finding.message == (
        "2 test rows are dated before the last training date, 2026-02-09 "
        "(17% of the test file; the first test date is 2026-01-02)"
    )


def test_a_test_that_starts_the_day_the_training_ends_is_in_order() -> None:
    train, test = healthy()
    last = max(date.fromisoformat(r["date"]) for r in train)
    test[0]["date"] = last.isoformat()
    assert named(audit(train, test), "time-order") == []


def test_a_time_column_that_holds_no_dates_is_an_error_of_use() -> None:
    train, test = healthy()
    for row in train:
        row["date"] = "morning"
    with pytest.raises(TableError, match="--time 'date' of train.csv does not hold dates"):
        audit(train, test)


# --- using it wrong -----------------------------------------------------------------------------------------------


def test_a_column_that_is_not_there_is_an_error_that_lists_the_columns() -> None:
    train, test = healthy()
    with pytest.raises(TableError, match=r"--group 'nope' is not a column of train\.csv; its columns are: subject_id,"):
        audit(train, test, groups=("nope",))
    with pytest.raises(TableError, match="--target 'nope' is not a column of train.csv"):
        audit(train, test, target="nope")
    with pytest.raises(TableError, match="--time 'nope' is not a column of train.csv"):
        audit(train, test, time="nope")
    with pytest.raises(TableError, match="--ignore 'nope' is not a column of either file"):
        audit(train, test, ignore=("nope",))


def test_the_target_does_not_have_to_be_in_the_test_file() -> None:
    train, test = healthy()
    for row in test:
        del row["qc"]
    found = audit(train, test)  # the checks that need the target in the test file are simply not made
    assert named(found, "columns-differ")
    assert named(found, "identical-rows") == []


# --- the examples ---------------------------------------------------------------------------------------------------


def examples() -> tuple[Table, Table]:
    return read_table(EXAMPLES / "train.csv"), read_table(EXAMPLES / "test.csv")


def test_the_examples_leak_in_the_ways_they_were_made_to() -> None:
    train, test = examples()
    report = audit_leakage(train, test, Options(("subject_id",), "qc", "date"))
    assert sorted((f.check, f.severity, f.column or "") for f in report.findings) == [
        ("group-overlap", "error", ""),
        ("identical-rows", "error", ""),
        ("target-determined", "warning", "site"),
        ("target-separated", "warning", "artifact_score"),
        ("time-order", "warning", ""),
    ]


def test_without_options_only_the_rows_can_be_compared() -> None:
    train, test = examples()
    report = audit_leakage(train, test, Options())
    assert [f.check for f in report.findings] == ["identical-rows"]
