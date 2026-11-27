"""The checks that need no rules. Each one has a case where it speaks and a case where it must keep quiet."""

from __future__ import annotations

import pytest

from table_audit import profile as profile_module
from table_audit.profile import profile
from table_audit.reader import TableError, read_table

from .helpers import EXAMPLES, checks, make, named, one


def clean_table() -> str:
    """Thirty rows of tidy data, in the style of an Excel file saved in Brazil."""
    lines = ["subject_id;visit;sex;age;grip_force_n;visit_date"]
    for i in range(30):
        sex = "F" if i % 2 else "M"
        lines.append(
            f"P{i + 1:04d};{i % 3 + 1};{sex};{20 + (i * 7) % 40};{30 + (i * 13) % 25},{i % 10};2026-02-{i % 28 + 1:02d}"
        )
    return "\n".join(lines) + "\n"


def test_a_tidy_table_gets_no_findings_and_a_profile_of_each_column() -> None:
    report = profile(make(clean_table()))
    assert report.findings == []
    kinds = {c.name: c.kind for c in report.columns}
    assert kinds == {
        "subject_id": "text",
        "visit": "int",
        "sex": "text",
        "age": "int",
        "grip_force_n": "float",
        "visit_date": "date",
    }
    visit = next(c for c in report.columns if c.name == "visit")
    assert (visit.missing, visit.distinct, visit.minimum, visit.maximum) == (0, 3, "1", "3")
    assert report.facts[0] == ("file", "t.csv")
    assert report.facts[1] == ("shape", "30 rows x 6 columns")
    assert "delimiter ';' (detected), decimal ',' (detected), encoding utf-8" in report.facts[2][1]


def test_the_table_the_examples_ship_is_found_dirty_in_the_ways_it_was_made_dirty() -> None:
    report = profile(read_table(EXAMPLES / "participants.csv"), ("subject_id", "visit"))
    found = sorted((f.check, f.column or "") for f in report.findings)
    assert found == [
        ("date-formats", "visit_date"),
        ("duplicate-key", ""),
        ("duplicate-rows", ""),
        ("empty-rows", ""),
        ("label-variants", "sex"),
        ("mixed-types", "grip_force_n"),
        ("mixed-types", "visit_date"),
        ("outliers", "age"),
        ("placeholder", "grip_force_n"),
        ("ragged-rows", ""),
        ("whitespace", "sex"),
    ]


# --- structure ---------------------------------------------------------------------------------------------------


def test_a_column_without_a_name_and_a_name_used_twice_are_errors() -> None:
    findings = profile(make("a,,a\n1,2,3\n")).findings
    assert {f.severity for f in named(findings, "column-name")} == {"error"}
    messages = [f.message for f in named(findings, "column-name")]
    assert "column 2 has no name" in messages
    assert "the name 'a' is used by 2 columns (1, 3)" in messages


def test_rows_with_the_wrong_number_of_cells_are_an_error_with_their_lines() -> None:
    finding = one(profile(make("a,b,c\n1,2\n3,4,5\n6,7,8,9\n")).findings, "ragged-rows")
    assert finding.severity == "error" and finding.count == 2 and finding.lines == (2, 4)
    assert finding.message == "2 rows do not have the 3 cells of the header (padded or cut to fit)"


def test_one_row_with_the_wrong_number_of_cells_is_said_in_the_singular() -> None:
    finding = one(profile(make("a,b\n1,2,3\n4,5\n")).findings, "ragged-rows")
    assert finding.message == "1 row does not have the 2 cells of the header (padded or cut to fit)"


def test_blank_lines_are_information_and_empty_rows_a_warning() -> None:
    findings = profile(make("a,b\n1,2\n\n,\n3,4\n")).findings
    assert one(findings, "blank-lines").severity == "info"
    assert one(findings, "blank-lines").message == "1 blank line was skipped"
    empty = one(findings, "empty-rows")
    assert empty.severity == "warning" and empty.lines == (4,)
    assert empty.message == "1 row has no value at all"


def test_a_file_with_a_header_and_nothing_else_is_a_warning() -> None:
    assert checks(profile(make("a,b\n")).findings) == ["no-rows"]


def test_the_encoding_note_is_information() -> None:
    from table_audit.reader import parse_table

    report = profile(parse_table("nome;x\nJoão;1\n".encode("cp1252"), "excel.csv"))
    assert [(f.check, f.severity) for f in report.findings] == [("encoding", "info")]


# --- columns -----------------------------------------------------------------------------------------------------


def test_a_column_with_nothing_in_it_is_a_warning_and_has_the_kind_empty() -> None:
    report = profile(make("a,b\n1,\n2,NA\n"))
    assert one(report.findings, "empty-column", "b").severity == "warning"
    assert next(c for c in report.columns if c.name == "b").kind == "empty"


def test_numbers_with_a_few_words_among_them_are_reported_as_the_words() -> None:
    rows = "\n".join(f"{i}" for i in range(1, 20)) + "\nND\n"
    report = profile(make("x\n" + rows))
    finding = one(report.findings, "mixed-types", "x")
    assert finding.message == "1 value is not a number" and finding.examples == ("ND",) and finding.lines == (21,)
    assert next(c for c in report.columns if c.name == "x").kind == "int"


def test_a_number_written_with_the_other_decimal_separator_is_named_as_that() -> None:
    base = "\n".join(f"{i},5" for i in range(1, 41))

    def message(*extra: str) -> str:
        table = make("x\n" + "\n".join([base, *extra]) + "\n", decimal=",")
        return one(profile(table).findings, "mixed-types", "x").message

    assert message("7.5", "8.5", "ND") == "3 values are not numbers (2 written with '.' instead of ',')"
    assert message("7.5", "8.5") == "2 values are not numbers (all written with '.' instead of ',')"
    assert message("7.5") == "1 value is not a number (written with '.' instead of ',')"
    assert message("ND") == "1 value is not a number"


def test_a_column_that_is_a_mix_has_no_clear_type() -> None:
    body = "\n".join(["1", "2", "3", "4", "5", "6", "a", "b", "c", "d"])
    report = profile(make("x\n" + body))
    finding = one(report.findings, "mixed-types", "x")
    assert finding.message == "no clear type: 60% number, 40% text"
    assert next(c for c in report.columns if c.name == "x").kind == "mixed"


def test_a_column_of_true_and_false_with_a_yes_among_them() -> None:
    body = "\n".join(["true", "false"] * 10) + "\nyes\n"
    finding = one(profile(make("x\n" + body)).findings, "mixed-types", "x")
    assert finding.message == "1 value is not a true/false value"


def test_zero_padded_codes_are_text_and_not_numbers() -> None:
    report = profile(make("code\n" + "\n".join(f"{i:03d}" for i in range(1, 12)) + "\n"))
    assert next(c for c in report.columns if c.name == "code").kind == "text"
    assert report.findings == []


def test_a_column_that_is_mostly_empty_is_a_warning_only_from_half_up() -> None:
    half = profile(make("a,b\n" + "1,\n2,x\n" * 5))
    assert one(half.findings, "missing", "b").message == "no value in 5 of 10 rows (50%)"
    less = profile(make("a,b\n" + "1,\n2,x\n2,y\n" * 3))
    assert named(less.findings, "missing") == []


def test_a_column_with_one_value_everywhere_is_information_only_when_nothing_is_missing() -> None:
    assert one(profile(make("a,b\n1,x\n2,x\n3,x\n")).findings, "constant", "b").severity == "info"
    assert named(profile(make("a,b\n1,x\n2,x\n3,\n")).findings, "constant") == []
    assert named(profile(make("a,b\n1,x\n")).findings, "constant") == []  # one row: everything is constant


def test_spaces_at_the_ends_of_a_value_are_a_warning_with_the_value() -> None:
    finding = one(profile(make("a;b\n1;M \n2;F\n3; M\n4;F\n")).findings, "whitespace", "b")
    assert finding.count == 2 and finding.lines == (2, 4)
    assert finding.examples == ("M ", " M")
    assert finding.message == "2 values have spaces at the start or end (a join will miss them)"


def test_labels_that_differ_in_case_accents_or_spaces_are_variants() -> None:
    body = "\n".join(["Não"] * 5 + ["nao"] * 3 + ["NÃO"] * 1 + ["Sim"] * 6)
    finding = one(profile(make("x\n" + body)).findings, "label-variants", "x")
    assert finding.count == 4  # the rows with the less common spellings
    assert finding.examples == ("Não / nao / NÃO",)
    assert finding.message == "1 label is written in more than one way (case, accents or spaces)"


def test_different_labels_and_numbers_are_not_variants() -> None:
    assert named(profile(make("x\nF\nM\nF\nM\nOther\n")).findings, "label-variants") == []
    assert named(profile(make("x\n1\n1.0\n1\n")).findings, "label-variants") == []


def test_a_text_column_with_many_distinct_values_is_not_a_list_of_categories() -> None:
    names = [f"Name {i}" for i in range(profile_module.CATEGORY_MAX_DISTINCT + 1)]
    body = "\n".join([*names, "NAME 0"])
    assert named(profile(make("x\n" + body)).findings, "label-variants") == []


# --- numbers -----------------------------------------------------------------------------------------------------


def numbers_with(*extra: str, around: int = 100, count: int = 24) -> str:
    base = [str(around + (i % 7) - 3) for i in range(count)]  # 97..103
    return "x\n" + "\n".join([*base, *extra]) + "\n"


def test_a_code_for_missing_far_from_the_rest_is_a_placeholder() -> None:
    finding = one(profile(make(numbers_with("-999", "-999"))).findings, "placeholder", "x")
    assert finding.count == 2 and finding.examples == ("-999",)
    assert finding.message == "2 values look like a code for 'missing', not a measurement"
    single = one(profile(make(numbers_with("9999"))).findings, "placeholder", "x")
    assert single.message == "1 value looks like a code for 'missing', not a measurement"


def test_a_sentinel_among_values_of_its_own_size_is_not_a_placeholder() -> None:
    report = profile(make(numbers_with(around=-99)))  # a column whose values really are around -99
    assert named(report.findings, "placeholder") == []
    constant = profile(make("x\n" + "999\n" * 12))
    assert named(constant.findings, "placeholder") == []


def test_few_numbers_are_not_enough_to_call_anything_a_placeholder() -> None:
    assert named(profile(make("x\n5\n6\n5\n6\n5\n6\n-999\n")).findings, "placeholder") == []  # 7 numbers
    assert one(profile(make("x\n5\n6\n5\n6\n5\n6\n5\n-999\n")).findings, "placeholder", "x")  # 8 numbers


def test_a_value_far_from_the_rest_is_an_outlier_only_with_enough_numbers() -> None:
    finding = one(profile(make(numbers_with("500"))).findings, "outliers", "x")
    assert finding.severity == "info" and finding.examples == ("500",) and finding.lines == (26,)
    assert finding.message == "1 value is more than 6 robust deviations from the median"
    few = "x\n" + "\n".join(["10", "11", "10", "12", "11", "10", "11", "500"]) + "\n"
    assert named(profile(make(few)).findings, "outliers") == []


def test_a_placeholder_is_not_also_an_outlier() -> None:
    findings = profile(make(numbers_with("-999", "500"))).findings
    assert one(findings, "placeholder", "x").examples == ("-999",)
    assert one(findings, "outliers", "x").examples == ("500",)


def test_the_thresholds_are_the_ones_the_module_names() -> None:
    assert profile_module.OUTLIER_Z == 6.0 and profile_module.MIN_FOR_OUTLIERS == 20
    assert profile_module.MIXED_SHARE == 0.9 and profile_module.MANY_MISSING == 0.5


# --- dates -------------------------------------------------------------------------------------------------------


def test_dates_written_in_two_ways_are_a_warning_with_the_less_common_way_listed() -> None:
    body = "\n".join([f"2026-03-{d:02d}" for d in range(1, 10)] + ["10/03/2026", "11/03/2026"])
    finding = one(profile(make("d\n" + body)).findings, "date-formats", "d")
    assert finding.message == "dates are written in two ways: 9 like 2026-03-02 and 2 like 02/03/2026"
    assert finding.count == 2 and finding.examples == ("10/03/2026", "11/03/2026")


def test_dates_with_and_without_a_time_are_the_same_way() -> None:
    body = "2026-03-01\n2026-03-02 10:30\n2026-03-03T08:00:00Z\n"
    assert named(profile(make("d\n" + body)).findings, "date-formats") == []


def test_an_impossible_date_is_not_a_date() -> None:
    body = "\n".join(f"2026-03-{d:02d}" for d in range(1, 12)) + "\n2026-02-30\n"
    finding = one(profile(make("d\n" + body)).findings, "mixed-types", "d")
    assert finding.message == "1 value is not a date" and finding.examples == ("2026-02-30",)


# --- duplicates --------------------------------------------------------------------------------------------------


def test_a_row_that_repeats_an_earlier_one_is_a_warning_with_its_line() -> None:
    finding = one(profile(make("a,b\n1,x\n2,y\n1, x\n")).findings, "duplicate-rows")
    assert finding.count == 1 and finding.lines == (4,)
    assert finding.message == "1 row repeats an earlier row exactly (spaces at the ends ignored)"


def test_empty_rows_are_not_repeated_rows() -> None:
    findings = profile(make("a,b\n1,x\n,\n,\n")).findings
    assert named(findings, "duplicate-rows") == []
    assert one(findings, "empty-rows").message == "2 rows have no value at all"


def test_a_key_that_is_not_unique_is_an_error() -> None:
    table = make("id,visit,v\nA,1,x\nA,2,y\nA,1,z\nB,1,w\nB,1,q\n")
    finding = one(profile(table, ("id", "visit")).findings, "duplicate-key")
    assert finding.severity == "error" and finding.count == 2 and finding.lines == (4, 6)
    assert finding.examples == ("A/1", "B/1")
    assert finding.message == "(id, visit) is not unique: 2 keys appear more than once (2 extra rows)"
    assert named(profile(table, ("id", "visit", "v")).findings, "duplicate-key") == []


def test_spaces_at_the_ends_do_not_make_two_keys_of_one() -> None:
    finding = one(profile(make("id,v\nA,1\nA ,2\n"), ("id",)).findings, "duplicate-key")
    assert finding.examples == ("A",) and finding.lines == (3,)


def test_a_key_that_is_not_a_column_is_a_table_error() -> None:
    with pytest.raises(TableError, match="no column 'nope'"):
        profile(make("a,b\n1,2\n"), ("nope",))
