"""Reading a file: the delimiter, the decimal separator, the encoding, and the rows that are not what they should be."""

from __future__ import annotations

from pathlib import Path

import pytest

from table_audit.reader import TableError, detect_decimal, detect_delimiter, parse_table, read_table

from .helpers import make


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_the_delimiter_is_found(delimiter: str) -> None:
    text = "\n".join(delimiter.join(row) for row in [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]])
    table = make(text)
    assert table.delimiter == delimiter and table.delimiter_detected
    assert table.columns == ["a", "b", "c"] and table.rows == [["1", "2", "3"], ["4", "5", "6"]]


def test_a_delimiter_that_is_given_is_used_and_reported_as_given() -> None:
    table = make("a|b;c\n1|2;3\n", delimiter=";")
    assert table.delimiter == ";" and not table.delimiter_detected
    assert table.columns == ["a|b", "c"]


def test_a_comma_inside_a_number_does_not_make_the_comma_the_delimiter() -> None:
    assert detect_delimiter("a;b\n1,5;2,5\n3,5;4,5\n") == ";"


def test_a_file_with_one_column_is_one_column_even_when_its_numbers_have_a_decimal_comma() -> None:
    table = make("a\n1\n2\n")
    assert table.columns == ["a"] and table.rows == [["1"], ["2"]]
    comma = make("x\n1,5\n2,25\n")
    assert comma.columns == ["x"] and comma.rows == [["1,5"], ["2,25"]] and comma.ragged == []
    assert comma.decimal == ","
    assert detect_delimiter("x\n1,5\n2;5\n") == "\t"  # a character that is not in the data
    assert detect_delimiter("x\n1,5\n2;5\n3\t5\n4|5\n") == ","  # none is free: the usual one


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([["1,5", "2,25"], ["3,5", "x"]], ","),
        ([["1.5", "2.25"], ["3.5", "x"]], "."),
        ([["1", "2"], ["3", "4"]], "."),  # whole numbers say nothing: the default
        ([["1,5", "1.5", "2.5"]], "."),  # a tie goes to the point
        ([["a", "b"]], "."),
    ],
)
def test_the_decimal_separator_is_the_commoner_one(rows: list[list[str]], expected: str) -> None:
    assert detect_decimal(rows) == expected


def test_a_decimal_separator_that_is_given_wins() -> None:
    table = make("a;b\n1,5;2,5\n", decimal=".")
    assert table.decimal == "." and not table.decimal_detected


def test_a_byte_order_mark_is_not_part_of_the_first_column_name() -> None:
    table = parse_table(b"\xef\xbb\xbfid;x\n1;2\n", "bom.csv")
    assert table.columns == ["id", "x"] and table.encoding == "utf-8" and not table.notes


def test_a_file_from_excel_in_brazil_is_read_as_the_windows_code_page_and_says_so() -> None:
    data = "nome;faixa\nJoão;18–30\nAçucena;31–40\n".encode("cp1252")  # the dash is 0x96, which latin-1 does not have
    table = parse_table(data, "excel.csv")
    assert table.encoding == "cp1252"
    assert table.column("nome") == ["João", "Açucena"] and table.column("faixa") == ["18–30", "31–40"]
    assert len(table.notes) == 1 and "cp1252" in table.notes[0]


def test_bytes_that_the_windows_code_page_does_not_have_fall_back_to_latin_1() -> None:
    table = parse_table(b"a;b\n\x81;1\n", "odd.csv")  # 0x81 is undefined in cp1252
    assert table.encoding == "latin-1" and table.rows == [["\x81", "1"]]


def test_an_encoding_that_is_given_is_the_only_one_tried() -> None:
    with pytest.raises(TableError, match="cannot read the file as utf-8"):
        parse_table("João\n1\n".encode("cp1252"), "x.csv", encoding="utf-8")
    with pytest.raises(TableError, match="cannot read the file as nonsense"):
        parse_table(b"a\n1\n", "x.csv", encoding="nonsense")
    assert parse_table("a\nJoão\n".encode("cp1252"), "x.csv", encoding="cp1252").rows == [["João"]]


def test_short_and_long_rows_are_padded_or_cut_and_remembered() -> None:
    table = make("a,b,c\n1,2\n3,4,5\n6,7,8,9\n")
    assert table.rows == [["1", "2", ""], ["3", "4", "5"], ["6", "7", "8"]]
    assert table.ragged == [(2, 2), (4, 4)]
    assert all(len(row) == 3 for row in table.rows)


def test_blank_lines_are_skipped_and_counted_and_do_not_move_the_line_numbers() -> None:
    table = make("a,b\n1,2\n\n\n3,4\n")
    assert table.rows == [["1", "2"], ["3", "4"]]
    assert table.blank_lines == 2
    assert table.lines == [2, 5]


def test_quoted_cells_may_hold_the_delimiter_quotes_and_line_breaks() -> None:
    table = make('a,b\n"x,y",2\n"say ""hi""","line one\nline two"\n')
    assert table.rows == [["x,y", "2"], ['say "hi"', "line one\nline two"]]
    assert table.lines == [2, 4]  # a row that spans lines (3 and 4) is placed at its last line


def test_names_of_the_columns_lose_the_spaces_around_them() -> None:
    assert make(" a , b \n1,2\n").columns == ["a", "b"]


def test_a_file_with_only_a_header_has_no_rows() -> None:
    table = make("a,b\n")
    assert table.columns == ["a", "b"] and table.rows == []


@pytest.mark.parametrize("data", [b"", b"   \n\n  \n", b"\xef\xbb\xbf"])
def test_an_empty_file_is_refused(data: bytes) -> None:
    with pytest.raises(TableError, match="is empty"):
        parse_table(data, "nothing.csv")


def test_options_that_make_no_sense_are_refused() -> None:
    with pytest.raises(TableError, match="one character"):
        make("a\n1\n", delimiter="ab")
    with pytest.raises(TableError, match="decimal separator"):
        make("a\n1\n", decimal=";")


def test_a_list_of_missing_words_replaces_the_built_in_one_but_keeps_the_empty_cell() -> None:
    default = make("a\n1\n")
    assert {"", "na", "null", "-", "?"} <= default.missing
    custom = make("a\n1\n", missing=("ND", " Sem Dado "))
    assert custom.missing == frozenset({"", "nd", "sem dado"})
    nothing = make("a\n1\n", missing=())
    assert nothing.missing == frozenset({""})


def test_present_cells_leave_out_the_missing_ones_and_keep_their_lines() -> None:
    table = make("a,b\n1,x\nNA,y\n,z\n4,\n")
    assert table.present("a") == [(2, "1"), (5, "4")]
    assert table.present("b") == [(2, "x"), (3, "y"), (4, "z")]


def test_an_unknown_column_is_an_error_that_lists_the_columns() -> None:
    table = make("a,b\n1,2\n", name="data.csv")
    with pytest.raises(TableError, match=r"data\.csv has no column 'c'; its columns are: a, b"):
        table.index("c")
    assert table.has("a") and not table.has("c")


def test_a_name_used_twice_is_found_at_its_first_place() -> None:
    table = make("a,a,b\n1,2,3\n")
    assert table.index("a") == 0 and table.column("a") == ["1"]


def test_a_file_that_does_not_exist_is_a_table_error(tmp_path: Path) -> None:
    with pytest.raises(TableError, match="cannot read"):
        read_table(tmp_path / "missing.csv")


def test_a_file_is_read_with_its_name_and_the_options(tmp_path: Path) -> None:
    path = tmp_path / "participants.csv"
    path.write_bytes(b"id;x\r\nP1;1,5\r\nP2;2,5\r\n")
    table = read_table(path)
    assert table.name == "participants.csv"
    assert table.delimiter == ";" and table.decimal == "," and table.rows == [["P1", "1,5"], ["P2", "2,5"]]


def test_a_cell_larger_than_the_csv_modules_limit_is_a_table_error_with_the_line() -> None:
    huge = "a;b\n1;2\n" + "x" * 200_000 + ";3\n"
    with pytest.raises(TableError, match=r"big\.csv, line 3: field larger than field limit"):
        parse_table(huge.encode(), "big.csv")
