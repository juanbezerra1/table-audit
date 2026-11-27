"""Rules: what a rules file may say, what is said when it is wrong, and what each rule finds."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from table_audit.rules import RulesError, apply_rules, load_rules, parse_rules

from .helpers import EXAMPLES, checks, make, named, one

TABLE = """id;visit;sex;age;weight;day
P0001;1;F;25;60,5;2026-01-10
P0002;1;M;31;80;2026-01-11
P0003;2;F;17;55;2025-12-31
P0004;1;f;40;ND;2026-01-12
P0005;1;M;91;70;02/01/2026
"""


def findings(spec: dict[str, Any], text: str = TABLE, **options: Any) -> list[Any]:
    return apply_rules(make(text, **options), parse_rules(spec))


def column(**rule: Any) -> dict[str, Any]:
    return {"columns": {"x": rule}}


# --- a rules file that is wrong ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ([], "the rules must be a JSON object"),
        ({}, "the rules say nothing"),
        ({"columns": {}}, "the rules say nothing"),
        ({"colums": {}}, "rules: unknown key 'colums'; did you mean 'columns'?"),
        ({"columns": []}, "columns: must be an object with one entry per column"),
        ({"columns": {"a": 5}}, 'columns.a: must be an object such as {"type": "int"}'),
        ({"columns": {"a": {"maximum": 5}}}, "columns.a: unknown key 'maximum'; did you mean 'max'?"),
        ({"columns": {"a": {"banana": 5}}}, "columns.a: unknown key 'banana'; the keys are type, not_null,"),
        (
            {"columns": {"a": {"type": "integer"}}},
            "columns.a.type: must be one of int, float, number, date, bool, text, not 'integer'",
        ),
        ({"columns": {"a": {"type": "text", "min": 1}}}, "columns.a: min and max need a type of int, float,"),
        ({"columns": {"a": {"min": "x"}}}, "columns.a.min: a date is written 2026-03-02, not 'x'"),
        ({"columns": {"a": {"type": "date", "min": 5}}}, "columns.a.min: a date is written 2026-03-02, not 5"),
        ({"columns": {"a": {"type": "int", "min": "5"}}}, "columns.a.min: must be a number, not '5'"),
        ({"columns": {"a": {"type": "int", "min": True}}}, "columns.a.min: must be a number, not True"),
        ({"columns": {"a": {"type": "int", "min": 5, "max": 1}}}, "columns.a: min is greater than max"),
        (
            {"columns": {"a": {"type": "date", "min": "2026-02-01", "max": "2026-01-01"}}},
            "columns.a: min is greater than max",
        ),
        (
            {"columns": {"a": {"min": 1, "max": "2026-01-01"}}},
            "columns.a: min and max must both be numbers or both be dates",
        ),
        ({"columns": {"a": {"allowed": "F"}}}, "columns.a.allowed: must be a list of texts or whole numbers"),
        ({"columns": {"a": {"allowed": [1.5]}}}, "columns.a.allowed: must be a list of texts or whole numbers"),
        ({"columns": {"a": {"allowed": [True]}}}, "columns.a.allowed: must be a list of texts or whole numbers"),
        ({"columns": {"a": {"pattern": 5}}}, "columns.a.pattern: must be a text"),
        ({"columns": {"a": {"pattern": "("}}}, "columns.a.pattern: not a valid regular expression"),
        ({"columns": {"a": {"not_null": "yes"}}}, "columns.a.not_null: must be true or false"),
        ({"columns": {"a": {"unique": 1}}}, "columns.a.unique: must be true or false"),
        ({"unique": ["a"]}, "unique: must be a list of lists of column names"),
        ({"unique": [[]]}, "unique: must be a list of lists of column names"),
        ({"unique": [[1]]}, "unique: must be a list of lists of column names"),
        ({"columns": {"a": {}}, "no_extra_columns": 1}, "rules.no_extra_columns: must be true or false"),
    ],
)
def test_a_mistake_in_the_rules_is_reported_with_the_place_where_it_is(spec: object, message: str) -> None:
    with pytest.raises(RulesError) as error:
        parse_rules(spec)
    assert message in str(error.value)


def test_a_rules_file_that_cannot_be_read_or_is_not_json_says_so(tmp_path: Path) -> None:
    with pytest.raises(RulesError, match="cannot read"):
        load_rules(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"columns": {', encoding="utf-8")
    with pytest.raises(RulesError, match=r"bad\.json is not valid JSON"):
        load_rules(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text('{"columns": {"age": {"maximum": 5}}}', encoding="utf-8")
    with pytest.raises(RulesError, match=r"wrong\.json: columns\.age: unknown key 'maximum'"):
        load_rules(wrong)


def test_a_rules_file_saved_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"columns": {"a": {"type": "int"}}}).encode())
    assert load_rules(path).columns[0].name == "a"


def test_the_rules_of_the_examples_say_what_the_readme_says_they_say() -> None:
    rules = load_rules(EXAMPLES / "rules.json")
    by_name = {rule.name: rule for rule in rules.columns}
    assert list(by_name) == ["subject_id", "visit", "sex", "age", "grip_force_n", "visit_date"]
    assert by_name["age"].minimum == 18 and by_name["age"].maximum == 90
    assert by_name["visit_date"].minimum == date(2026, 1, 1) and by_name["visit_date"].maximum is None
    assert by_name["sex"].allowed == ("F", "M")
    assert rules.unique == (("subject_id", "visit"),) and not rules.no_extra_columns


# --- what the rules find ------------------------------------------------------------------------------------------


def test_a_table_that_follows_the_rules_has_no_findings() -> None:
    spec = {
        "columns": {
            "id": {"type": "text", "not_null": True, "unique": True, "pattern": r"P\d{4}"},
            "visit": {"type": "int", "min": 1, "max": 2},
            "sex": {"allowed": ["F", "M", "f"]},
            "age": {"type": "int", "min": 17, "max": 91},
        },
        "unique": [["id", "visit"]],
    }
    assert findings(spec) == []


def test_a_column_the_rules_ask_for_and_the_table_lacks_is_an_error() -> None:
    found = findings({"columns": {"zzz": {"type": "int"}, "age": {"type": "int"}}})
    assert [f.message for f in found] == ["the rules ask for a column 'zzz' that the table does not have"]
    assert found[0].check == "missing-column" and found[0].severity == "error"


def test_extra_columns_are_an_error_only_when_the_rules_say_so() -> None:
    spec: dict[str, Any] = {"columns": {"id": {}, "visit": {}}}
    assert findings(spec) == []
    found = findings({**spec, "no_extra_columns": True})
    assert [f.message for f in found] == ["columns that the rules do not mention: sex, age, weight, day"]
    assert checks(found) == ["extra-column"] and found[0].severity == "error"


def test_not_null_counts_empty_cells_and_the_words_for_missing() -> None:
    text = "a,b\n1,x\n,y\nNA,z\n4,w\n"
    found = one(findings({"columns": {"a": {"not_null": True}}}, text), "not-null", "a")
    assert found.lines == (3, 4) and found.message == "2 cells are empty or missing"
    only = one(findings({"columns": {"a": {"not_null": True}}}, "a,b\n1,x\n,y\n3,z\n"), "not-null")
    assert only.count == 1 and only.message == "1 cell is empty or missing"
    custom = findings({"columns": {"a": {"not_null": True}}}, text, missing=("ND",))
    assert [f.lines for f in custom] == [(3,)]  # "NA" is a value when the words for missing are changed


def test_a_type_is_checked_and_the_cells_that_fail_it_are_not_checked_again() -> None:
    spec = {"columns": {"weight": {"type": "float", "min": 0, "max": 70}}}
    found = findings(spec, decimal=",")
    wrong = one(found, "wrong-type", "weight")
    assert wrong.examples == ("ND",) and wrong.message == "1 value is not a number"
    over = one(found, "out-of-range", "weight")
    assert over.examples == ("80",)  # ND is not also "out of range"


@pytest.mark.parametrize(
    ("type_", "good", "bad"),
    [
        ("int", ["1", "-5", "+3", "007"], ["1.0", "1e3", "a", "1,5"]),
        ("float", ["1.5", "2", "-0.5", "1e3"], ["1,5", "a", "inf"]),
        ("number", ["1.5", "2"], ["1,5", "x"]),
        ("date", ["2026-03-02", "02/03/2026"], ["2026-02-30", "March", "03/25/2026"]),
        ("bool", ["true", "FALSE", "0", "1"], ["yes", "2", "t"]),
        ("text", ["anything", "123", "2026-03-02"], []),
    ],
)
def test_each_type_accepts_what_it_should_and_nothing_else(type_: str, good: list[str], bad: list[str]) -> None:
    text = "x\n" + "\n".join([*good, *bad]) + "\n"
    found = named(findings(column(type=type_), text, decimal="."), "wrong-type", "x")
    if bad:
        assert found and set(found[0].examples) == set(bad)
        assert found[0].count == len(bad)
    else:
        assert found == []


def test_a_range_with_both_bounds_says_which_side_each_value_is_on() -> None:
    finding = one(findings(column(type="int", min=18, max=90), "x\n17\n25\n91\n205\n18\n90\n"), "out-of-range")
    assert finding.message == "3 values are outside 18 to 90 (1 below, 2 above)"
    assert finding.examples == ("17", "91", "205") and finding.lines == (2, 4, 5)


def test_a_range_with_one_bound_says_so_in_words() -> None:
    low = one(findings(column(type="int", min=18), "x\n17\n18\n19\n"), "out-of-range")
    assert low.message == "1 value is below the minimum 18"
    high = one(findings(column(type="float", max=0.5), "x\n0.4\n0.5\n0.6\n0.7\n"), "out-of-range")
    assert high.message == "2 values are above the maximum 0.5"
    both_one_side = one(findings(column(type="int", min=1, max=3), "x\n0\n-1\n2\n"), "out-of-range")
    assert both_one_side.message == "2 values are outside 1 to 3"


def test_dates_have_ranges_too() -> None:
    spec = column(type="date", min="2026-01-01", max="2026-12-31")
    finding = one(findings(spec, "x\n2025-12-31\n2026-01-01\n31/12/2026\n2027-01-01\n"), "out-of-range")
    assert finding.message == "2 values are outside 2026-01-01 to 2026-12-31 (1 below, 1 above)"
    assert finding.examples == ("2025-12-31", "2027-01-01")
    assert one(findings(column(type="date", min="2026-01-01"), "x\n2025-12-31\n"), "out-of-range").message == (
        "1 value is below the minimum 2026-01-01"
    )


def test_a_range_without_a_type_follows_the_kind_of_its_bounds() -> None:
    assert one(findings(column(min=10), "x\n5\n15\n"), "out-of-range").examples == ("5",)
    assert one(findings(column(min="2026-01-01"), "x\n2025-05-05\n2026-05-05\n"), "out-of-range").examples == (
        "2025-05-05",
    )


def test_the_allowed_list_counts_each_value_and_lists_the_commonest() -> None:
    text = "x\nF\nM\nf\nf\nOther\nF\n"
    finding = one(findings(column(allowed=["F", "M"]), text), "not-allowed")
    assert finding.message == "3 values are not in the allowed list (F, M)"
    assert finding.examples == ("f", "Other")  # the commonest first
    assert one(findings(column(allowed=["F"]), "x\nM\n"), "not-allowed").message == (
        "1 value is not in the allowed list (F)"
    )
    long = findings(column(allowed=[str(i) for i in range(12)]), "x\nz\n")
    assert "(0, 1, 2, 3, 4, 5, 6, 7, ...)" in long[0].message


def test_whole_numbers_in_the_allowed_list_match_the_text_in_the_file() -> None:
    assert findings(column(allowed=[1, 2, 3]), "x\n1\n2\n3\n") == []
    assert one(findings(column(allowed=[1, 2, 3]), "x\n1\n4\n"), "not-allowed").examples == ("4",)


def test_a_pattern_must_match_the_whole_value() -> None:
    spec = column(pattern=r"P\d{4}")
    found = one(findings(spec, "x\nP0001\nP00012\nxP0001\np0001\nP12\n"), "pattern")
    assert found.examples == ("P00012", "xP0001", "p0001", "P12") and found.count == 4
    assert found.message == r"4 values do not match the pattern P\d{4}"
    assert one(findings(spec, "x\nP12\n"), "pattern").message == r"1 value does not match the pattern P\d{4}"


def test_a_column_that_must_be_unique_lists_the_repeated_values() -> None:
    found = one(findings(column(unique=True), "x\nA\nB\nA\nC\nB\nA\n"), "duplicate-key", "x")
    assert found.message == "values must be unique: 2 values appear more than once (3 extra rows)"
    assert found.examples == ("A", "B") and found.lines == (4, 6, 7)
    assert one(findings(column(unique=True), "x\nA\nA\n"), "duplicate-key").message == (
        "values must be unique: 1 value appears more than once (1 extra row)"
    )


def test_missing_cells_are_not_repeated_values() -> None:
    assert findings(column(unique=True), "x\nA\n\n\nB\n") == []


def test_a_set_of_columns_that_must_be_unique_together() -> None:
    text = "id,visit\nA,1\nA,2\nA,1\nB,1\n"
    found = one(findings({"unique": [["id", "visit"]]}, text), "duplicate-key")
    assert found.message == "(id, visit) is not unique: 1 key appears more than once (1 extra row)"
    assert found.examples == ("A/1",)
    assert findings({"unique": [["id"]]}, text)[0].message.startswith("(id) is not unique: 1 key appears")
    absent = findings({"unique": [["id", "nope"]]}, text)
    assert checks(absent) == ["missing-column"] and "nope" in absent[0].message


def test_the_examples_break_the_rules_in_the_ways_they_were_made_to() -> None:
    from table_audit.reader import read_table

    table = read_table(EXAMPLES / "participants.csv")
    found = apply_rules(table, load_rules(EXAMPLES / "rules.json"))
    assert sorted((f.check, f.column or "") for f in found) == [
        ("duplicate-key", ""),
        ("not-allowed", "sex"),
        ("not-null", "subject_id"),
        ("out-of-range", "age"),
        ("out-of-range", "grip_force_n"),
        ("out-of-range", "visit_date"),
        ("pattern", "subject_id"),
        ("wrong-type", "grip_force_n"),
        ("wrong-type", "visit_date"),
    ]
    assert {f.severity for f in found} == {"error"}


def test_a_range_on_a_column_without_a_type_does_not_call_the_words_wrong() -> None:
    # "abc" is not a number, but the rule does not say the column is one: only the numbers are compared
    found = one(findings(column(min=10), "x\nabc\n5\n15\n"), "out-of-range")
    assert found.examples == ("5",) and found.count == 1


def test_a_number_is_never_less_than_a_date() -> None:
    from table_audit.rules import _less

    assert not _less(1.0, date(2026, 1, 1)) and not _less(date(2026, 1, 1), 1.0)
    assert _less(1.0, 2.0) and _less(date(2026, 1, 1), date(2026, 1, 2)) and not _less(2.0, 1.0)
