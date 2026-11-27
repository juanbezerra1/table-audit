"""Reading a CSV file the way people really write them, with nothing but the standard library.

A file saved by Excel in Brazil uses ";" between cells, "," inside numbers and the Windows code page instead of UTF-8.
Nothing in the file says so, so this module looks: it detects the delimiter and the decimal separator, falls back to
the Windows code page when the bytes are not UTF-8, and tells the caller what it chose, so that the report can say so.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from .values import DEFAULT_MISSING

DELIMITERS = ",;\t|"
_COMMA_NUMBER = re.compile(r"[+-]?\d+,\d+")
_DOT_NUMBER = re.compile(r"[+-]?\d+\.\d+")


class TableError(Exception):
    """The file cannot be read as a table; the message says why, in words for the person who ran the command."""


@dataclass
class Table:
    """A table of text cells. Every row has exactly one cell per column (short rows are padded, long ones cut)."""

    name: str
    columns: list[str]
    rows: list[list[str]]
    lines: list[int]  # the line of the file each row ended on
    delimiter: str
    decimal: str
    encoding: str
    delimiter_detected: bool
    decimal_detected: bool
    missing: frozenset[str] = frozenset(DEFAULT_MISSING)
    ragged: list[tuple[int, int]] = field(default_factory=list)  # (line, cells the row really had)
    blank_lines: int = 0
    notes: list[str] = field(default_factory=list)

    def has(self, name: str) -> bool:
        return name in self.columns

    def index(self, name: str) -> int:
        """The position of a column (the first one, when a name is used twice)."""
        try:
            return self.columns.index(name)
        except ValueError:
            raise TableError(
                f"{self.name} has no column {name!r}; its columns are: {', '.join(self.columns)}"
            ) from None

    def column(self, name: str) -> list[str]:
        position = self.index(name)
        return [row[position] for row in self.rows]

    def present_at(self, position: int) -> list[tuple[int, str]]:
        """(line, text) of the cells of the column at this position that say something (not empty, not a token)."""
        return [
            (line, row[position])
            for line, row in zip(self.lines, self.rows, strict=True)
            if row[position].strip().casefold() not in self.missing
        ]

    def present(self, name: str) -> list[tuple[int, str]]:
        """The same, for a column by name."""
        return self.present_at(self.index(name))


def detect_delimiter(text: str) -> str:
    """The delimiter that splits the first lines into the same number of cells, or the one that splits most.

    A file whose header has no delimiter at all has one column; then the delimiter is a character that is not in
    the data, so that a decimal comma ("1,5") is not taken for a second cell.
    """
    lines = [line for line in text.splitlines()[:40] if line.strip()][:20]
    best, best_score = ",", (False, 0)
    for candidate in DELIMITERS:
        counts = [line.count(candidate) for line in lines]
        if not counts or min(counts) == 0:
            continue
        consistent = len(set(counts)) == 1
        score = (consistent, counts[0] if consistent else min(counts))
        if score > best_score:
            best, best_score = candidate, score
    if best_score == (False, 0) and lines and not any(c in lines[0] for c in DELIMITERS):
        sample = "".join(lines)
        best = next((c for c in ";\t|," if c not in sample), ",")
    return best


def detect_decimal(rows: list[list[str]]) -> str:
    """ "," when more cells look like 12,5 than like 12.5, otherwise "."."""
    commas = dots = 0
    for row in rows[:5000]:
        for cell in row:
            cell = cell.strip()
            if _COMMA_NUMBER.fullmatch(cell):
                commas += 1
            elif _DOT_NUMBER.fullmatch(cell):
                dots += 1
    return "," if commas > dots else "."


def decode(data: bytes, encoding: str | None) -> tuple[str, str, list[str]]:
    """The text, the encoding used, and a note when the file was not in the encoding that was expected."""
    if encoding:
        try:
            return data.decode(encoding), encoding, []
        except (UnicodeDecodeError, LookupError) as error:
            raise TableError(f"cannot read the file as {encoding}: {error}") from None
    try:
        return data.decode("utf-8-sig"), "utf-8", []
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
        used = "cp1252"
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        used = "latin-1"
    return (
        text,
        used,
        [f"the file is not UTF-8; it was read as {used} (Windows and Excel in Western Europe and Brazil)"],
    )


def parse_table(
    data: bytes,
    name: str,
    *,
    delimiter: str | None = None,
    decimal: str | None = None,
    encoding: str | None = None,
    missing: tuple[str, ...] | None = None,
) -> Table:
    """Make a Table from the bytes of a file. `name` is only what the report calls it."""
    if delimiter is not None and len(delimiter) != 1:
        raise TableError(f"the delimiter must be one character, not {delimiter!r}")
    if decimal is not None and decimal not in (".", ","):
        raise TableError(f"the decimal separator must be '.' or ',', not {decimal!r}")
    text, used_encoding, notes = decode(data, encoding)
    if not text.strip():
        raise TableError(f"{name} is empty")
    chosen = delimiter or detect_delimiter(text)

    records: list[tuple[int, list[str]]] = []
    blank = 0
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=chosen, quotechar='"')
    try:
        for record in reader:
            if not record:
                blank += 1
            else:
                records.append((reader.line_num, record))
    except csv.Error as error:
        raise TableError(f"{name}, line {reader.line_num}: {error}") from None
    if not records:
        raise TableError(f"{name} has no header row")

    header_line, header = records[0]
    columns = [cell.strip() for cell in header]
    body = records[1:]
    rows: list[list[str]] = []
    lines: list[int] = []
    ragged: list[tuple[int, int]] = []
    for line, record in body:
        if len(record) != len(columns):
            ragged.append((line, len(record)))
        rows.append((record + [""] * len(columns))[: len(columns)])
        lines.append(line)

    return Table(
        name=name,
        columns=columns,
        rows=rows,
        lines=lines,
        delimiter=chosen,
        decimal=decimal or detect_decimal(rows),
        encoding=used_encoding,
        delimiter_detected=delimiter is None,
        decimal_detected=decimal is None,
        missing=frozenset({"", *(DEFAULT_MISSING if missing is None else (m.strip().casefold() for m in missing))}),
        ragged=ragged,
        blank_lines=blank,
        notes=notes,
    )


def read_table(
    path: Path,
    *,
    delimiter: str | None = None,
    decimal: str | None = None,
    encoding: str | None = None,
    missing: tuple[str, ...] | None = None,
) -> Table:
    """Read a file; the options are those of parse_table."""
    try:
        data = path.read_bytes()
    except OSError as error:
        raise TableError(f"cannot read {path}: {error.strerror or error}") from None
    return parse_table(data, path.name, delimiter=delimiter, decimal=decimal, encoding=encoding, missing=missing)
