"""One cell at a time: what counts as a number, a date, a missing value or a label."""

from __future__ import annotations

from datetime import date

import pytest

from table_audit.values import (
    DEFAULT_MISSING,
    classify,
    date_style,
    fold,
    is_missing,
    is_zero_padded,
    parse_date,
    parse_integer,
    parse_number,
    show_number,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12", 12),
        ("-3", -3),
        ("+7", 7),
        (" 5 ", 5),
        ("007", 7),
        ("9" * 18, 10**18 - 1),
        ("9" * 19, None),  # too long to be a count: an identifier, or a float
        ("1.0", None),
        ("1e3", None),
        ("1 000", None),
        ("12abc", None),
        ("", None),
        ("-", None),
    ],
)
def test_parse_integer(text: str, expected: int | None) -> None:
    assert parse_integer(text) == expected


@pytest.mark.parametrize(
    ("text", "decimal", "expected"),
    [
        ("1.5", ".", 1.5),
        ("1,5", ",", 1.5),
        ("-0,5", ",", -0.5),
        (".5", ".", 0.5),
        (",5", ",", 0.5),
        ("1e3", ".", 1000.0),
        ("1,5e2", ",", 150.0),
        (" 2.25 ", ".", 2.25),
        ("1,5", ".", None),  # a decimal comma in a file that writes decimal points is a mistake, not a number
        ("1.5", ",", None),
        ("1,234.5", ".", None),  # no thousands separators
        ("1.234,5", ",", None),
        ("nan", ".", None),
        ("inf", ".", None),
        ("-inf", ".", None),
        ("1e999", ".", None),  # overflows to infinity
        ("1.5.2", ".", None),
        ("", ".", None),
        ("abc", ".", None),
    ],
)
def test_parse_number(text: str, decimal: str, expected: float | None) -> None:
    assert parse_number(text, decimal) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-03-02", date(2026, 3, 2)),
        ("2026-03-02 10:30", date(2026, 3, 2)),
        ("2026-03-02T10:30:15", date(2026, 3, 2)),
        ("2026-03-02T10:30:15Z", date(2026, 3, 2)),
        ("2026-03-02T10:30:15.123-03:00", date(2026, 3, 2)),
        ("02/03/2026", date(2026, 3, 2)),  # day first, as in Brazil: the 2nd of March
        (" 31/12/2025 ", date(2025, 12, 31)),
        ("2026-02-30", None),  # there is no 30th of February
        ("31/04/2026", None),
        ("03/25/2026", None),  # month first is not read: there is no 25th month
        ("2026-3-2", None),
        ("2026/03/02", None),
        ("02/03/26", None),
        ("", None),
        ("March", None),
    ],
)
def test_parse_date(text: str, expected: date | None) -> None:
    assert parse_date(text) == expected


def test_date_style_tells_the_two_ways_apart() -> None:
    assert date_style("2026-03-02") == "iso"
    assert date_style("2026-03-02 10:30") == "iso"
    assert date_style("02/03/2026") == "day-first"
    assert date_style("2026-02-30") is None
    assert date_style("hello") is None


@pytest.mark.parametrize(
    ("text", "decimal", "expected"),
    [
        ("true", ".", "bool"),
        ("FALSE", ".", "bool"),
        ("12", ".", "int"),
        ("12,5", ",", "float"),
        ("12,5", ".", "text"),
        ("1e3", ".", "float"),
        ("2026-03-02", ".", "date"),
        ("02/03/2026", ",", "date"),
        ("abc", ".", "text"),
        ("007", ".", "text"),  # zeros in front: an identifier, which a number would not keep
        ("0", ".", "int"),
        ("0.5", ".", "float"),
        ("-007", ".", "text"),
    ],
)
def test_classify(text: str, decimal: str, expected: str) -> None:
    assert classify(text, decimal) == expected


def test_zero_padded_numbers_are_identifiers() -> None:
    assert is_zero_padded("007") and is_zero_padded("00") and is_zero_padded("+012")
    assert not is_zero_padded("0") and not is_zero_padded("0.5") and not is_zero_padded("10") and not is_zero_padded("")


def test_fold_takes_case_accents_and_spaces_away() -> None:
    assert fold("Não ") == fold("nao") == fold("  NÃO") == "nao"
    assert fold("Sao   Paulo") == fold("são paulo")
    assert fold("F") != fold("M")


def test_missing_tokens_are_compared_without_case_or_spaces() -> None:
    tokens = frozenset(DEFAULT_MISSING)
    for text in ("", "  ", "NA", " n/a ", "NaN", "null", "None", "#N/A", "-", "?"):
        assert is_missing(text, tokens), text
    for text in ("0", "no", "nao", "--", "NAN1"):
        assert not is_missing(text, tokens), text


def test_show_number_writes_numbers_as_a_person_would() -> None:
    assert show_number(50.0) == "50"
    assert show_number(-999.0) == "-999"
    assert show_number(0.25) == "0.25"
    assert show_number(1234567.5) == "1.23457e+06"
    assert show_number(1e20) == "1e+20"
