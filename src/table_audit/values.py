"""One cell at a time: is it missing, is it a number, is it a date, what kind of thing is it.

Everything here works on text, because that is what a CSV file holds. The decimal separator is a parameter, not a
guess: "1,5" is a number in a file that writes decimal commas and a mistake in one that writes decimal points.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date

# What a cell may say instead of a value. Compared after trimming spaces and ignoring case.
DEFAULT_MISSING = ("", "na", "n/a", "nan", "null", "none", "#n/a", "-", "?")

BOOLEANS = ("true", "false")

_INTEGER = re.compile(r"[+-]?\d+")
_NUMBER = {
    ".": re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"),
    ",": re.compile(r"[+-]?(?:\d+(?:,\d+)?|,\d+)(?:[eE][+-]?\d+)?"),
}
_ISO_DATE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})(?:[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}(?::?\d{2})?)?)?"
)
_BR_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{4})")

# The longest run of digits that is still read as an integer; longer ones are floats (or ids).
MAX_INTEGER_DIGITS = 18


def is_missing(text: str, tokens: frozenset[str]) -> bool:
    """Whether the cell is empty or says "no value" in one of the ways in `tokens` (lower case)."""
    return text.strip().casefold() in tokens


def parse_integer(text: str) -> int | None:
    """The integer written in `text`, or None. "1.0" and "1e3" are not integers here."""
    stripped = text.strip()
    if not _INTEGER.fullmatch(stripped) or len(stripped.lstrip("+-")) > MAX_INTEGER_DIGITS:
        return None
    return int(stripped)


def parse_number(text: str, decimal: str = ".") -> float | None:
    """The number written in `text` with this decimal separator, or None.

    No thousands separators ("1,234.5" is not a number), no "nan" or "inf": a cell that says so is a missing
    value or a mistake, and either way it is not a measurement.
    """
    stripped = text.strip()
    if not _NUMBER[decimal].fullmatch(stripped):
        return None
    value = float(stripped.replace(",", "."))
    return value if math.isfinite(value) else None


def parse_date(text: str) -> date | None:
    """The day written in `text` as 2026-03-02 (with or without a time) or 02/03/2026 (day first), or None."""
    stripped = text.strip()
    match = _ISO_DATE.fullmatch(stripped)
    parts: tuple[int, int, int]
    if match:
        parts = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    else:
        br = _BR_DATE.fullmatch(stripped)
        if not br:
            return None
        parts = (int(br.group(3)), int(br.group(2)), int(br.group(1)))
    try:
        return date(*parts)
    except ValueError:
        return None


def date_style(text: str) -> str | None:
    """How a date is written: 'iso' (2026-03-02), 'day-first' (02/03/2026), or None when it is not a date."""
    stripped = text.strip()
    if parse_date(stripped) is None:
        return None
    return "iso" if _ISO_DATE.fullmatch(stripped) else "day-first"


def is_zero_padded(text: str) -> bool:
    """ "007" and "0012": digits that look like a number but are an identifier (a number would not keep the zeros)."""
    digits = text.strip().lstrip("+-")
    return len(digits) > 1 and digits.isdigit() and digits.startswith("0")


def classify(text: str, decimal: str = ".") -> str:
    """What one cell that is not missing is: 'bool', 'int', 'float', 'date' or 'text'."""
    stripped = text.strip()
    if stripped.casefold() in BOOLEANS:
        return "bool"
    if is_zero_padded(stripped):
        return "text"
    if parse_integer(stripped) is not None:
        return "int"
    if parse_number(stripped, decimal) is not None:
        return "float"
    if parse_date(stripped) is not None:
        return "date"
    return "text"


def fold(text: str) -> str:
    """Case, accents and runs of spaces taken away: 'Não ' and 'nao' are the same label."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


def show_number(value: float) -> str:
    """A number as a person would write it: 50, 0.25, 1.2e+06."""
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else f"{value:.6g}"
