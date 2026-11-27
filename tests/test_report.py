"""How a finding is worded and how a report is printed."""

from __future__ import annotations

import json

import pytest

from table_audit.report import (
    CHECKS,
    ColumnProfile,
    Finding,
    Report,
    counted,
    plural,
    render_checks,
    render_json,
    render_text,
    where,
)


def test_a_finding_must_have_a_severity_that_exists() -> None:
    with pytest.raises(ValueError, match="severity must be one of"):
        Finding("constant", "fatal", "nope")


def test_a_finding_must_be_of_a_check_that_is_listed_and_have_the_severity_the_list_gives() -> None:
    with pytest.raises(ValueError, match="'invented' is not in CHECKS"):
        Finding("invented", "info", "nope")
    with pytest.raises(ValueError, match="'constant' is a info in CHECKS, not a error"):
        Finding("constant", "error", "nope")
    assert Finding("constant", "info", "fine").check == "constant"


def test_every_check_has_a_severity_a_command_and_a_meaning() -> None:
    assert len(CHECKS) == 33
    for name, info in CHECKS.items():
        assert name == name.lower() and " " not in name
        assert info.severity in ("error", "warning", "info") and info.meaning and not info.meaning[0].isupper()
        assert set(info.commands.split(", ")) <= {"profile", "check", "leakage"}


def test_the_explanation_of_the_checks_lists_each_one_once() -> None:
    text = render_checks("0")
    lines = text.splitlines()
    assert lines[0] == "table-audit 0: explain"
    listed = [line.split()[0] for line in lines[4:]]
    assert listed == list(CHECKS)
    one = render_checks("0", "placeholder")
    assert one.splitlines()[0] == "table-audit 0: explain placeholder"
    assert "severity  warning" in one and "commands  profile" in one and CHECKS["placeholder"].meaning in one


@pytest.mark.parametrize(("n", "text"), [(0, "0 rows"), (1, "1 row"), (2, "2 rows")])
def test_plural(n: int, text: str) -> None:
    assert plural(n, "row") == text


@pytest.mark.parametrize(("n", "text"), [(0, "0 rows have"), (1, "1 row has"), (5, "5 rows have")])
def test_counted_agrees_the_verb(n: int, text: str) -> None:
    assert counted(n, "row has", "rows have") == text


def test_where_lists_the_first_lines_and_values_and_says_how_many_more() -> None:
    assert where(Finding("constant", "info", "m")) == ""
    assert where(Finding("constant", "info", "m", count=1, lines=(7,))) == " (line 7)"
    assert where(Finding("constant", "info", "m", count=2, lines=(7, 9))) == " (lines 7, 9)"
    assert (
        where(Finding("constant", "info", "m", count=8, lines=(1, 2, 3, 4, 5))) == " (lines 1, 2, 3, 4, 5 and 3 more)"
    )
    assert (
        where(Finding("constant", "info", "m", count=1, lines=(7,), examples=("a", "b"))) == " (line 7; e.g. 'a', 'b')"
    )
    assert where(Finding("constant", "info", "m", examples=("x",))) == " (e.g. 'x')"


def report(*findings: Finding) -> Report:
    return Report("profile", ["t.csv"], [("file", "t.csv")], [], list(findings))


def test_errors_come_first_then_warnings_then_information_each_in_the_order_found() -> None:
    first = Finding("constant", "info", "1")
    second = Finding("ragged-rows", "error", "2")
    third = Finding("whitespace", "warning", "3")
    fourth = Finding("column-name", "error", "4")
    assert [f.check for f in report(first, second, third, fourth).sorted_findings()] == [
        "ragged-rows",
        "column-name",
        "whitespace",
        "constant",
    ]


def test_the_counts_and_the_verdict() -> None:
    mixed = report(Finding("whitespace", "warning", "w"), Finding("constant", "info", "i"))
    assert mixed.counts() == {"error": 0, "warning": 1, "info": 1}
    assert not mixed.failed() and mixed.failed(strict=True)
    assert report(Finding("ragged-rows", "error", "e")).failed()
    assert not report(Finding("constant", "info", "i")).failed(strict=True)
    assert not report().failed(strict=True)


def test_the_text_report_with_nothing_to_say() -> None:
    text = render_text(report(), "1.2.3")
    assert text == "table-audit 1.2.3: profile\n\nfile  t.csv\n\nfindings: none\n\nresult: ok\n"


def test_the_text_report_lines_up_the_facts_and_the_columns() -> None:
    columns = [
        ColumnProfile("subject_id", "text", 2, 15),
        ColumnProfile("age", "int", 0, 12, "18", "205"),
    ]
    text = render_text(Report("profile", ["t.csv"], [("file", "t.csv"), ("shape", "3 rows x 2 columns")], columns), "0")
    lines = text.splitlines()
    assert lines[2:4] == ["file   t.csv", "shape  3 rows x 2 columns"]
    header = lines.index("columns") + 1
    assert lines[header] == "  column      kind  missing  distinct  min  max"
    assert lines[header + 1] == "  ----------  ----  -------  --------  ---  ---"
    assert lines[header + 2] == "  subject_id  text        2        15"
    assert lines[header + 3] == "  age         int         0        12   18  205"


def test_the_text_report_prints_each_finding_on_one_line_with_its_column_and_place() -> None:
    finding = Finding("whitespace", "warning", "1 value has spaces", "sex", 1, (29,), ("M ",))
    text = render_text(report(finding, Finding("ragged-rows", "error", "1 row is short", None, 1, (27,))), "0")
    assert "findings: 1 error, 1 warning\n" in text
    assert "  error   ragged-rows         1 row is short (line 27)\n" in text
    assert "  warning whitespace          sex: 1 value has spaces (line 29; e.g. 'M ')\n" in text
    assert text.index("ragged-rows") < text.index("whitespace")
    assert text.endswith("result: FAILED (1 error)\n")


def test_the_verdict_line_follows_strictness() -> None:
    warn = report(Finding("whitespace", "warning", "w"), Finding("empty-rows", "warning", "w"))
    assert render_text(warn, "0").endswith("result: ok (2 warnings)\n")
    assert render_text(warn, "0", strict=True).endswith("result: FAILED (2 warnings)\n")
    both = report(Finding("ragged-rows", "error", "e"), Finding("whitespace", "warning", "w"))
    assert render_text(both, "0").endswith("result: FAILED (1 error)\n")
    assert render_text(both, "0", strict=True).endswith("result: FAILED (1 error, 1 warning)\n")


def test_the_json_report_keeps_what_the_text_report_shortens() -> None:
    many = Finding("constant", "info", "m", None, 9, (1, 2, 3, 4, 5), ("a", "b", "c", "d", "e"))
    payload = json.loads(render_json(report(many), "0"))
    assert payload["findings"][0]["count"] == 9 and payload["findings"][0]["lines"] == [1, 2, 3, 4, 5]
    assert payload["findings"][0]["column"] is None
    assert payload["facts"] == {"file": "t.csv"} and payload["summary"]["failed"] is False


def test_the_json_report_escapes_what_is_not_ascii_so_that_any_console_can_carry_it() -> None:
    finding = Finding("constant", "info", "m", None, 1, (2,), ("Não",))
    raw = render_json(report(finding), "0")
    assert raw.isascii() and json.loads(raw)["findings"][0]["examples"] == ["Não"]
