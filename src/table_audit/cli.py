"""The table-audit command: profile a table, check it against rules, or look for leakage between two tables."""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from . import __version__
from .leakage import Options, audit_leakage
from .profile import audit_structure, facts, profile
from .reader import Table, TableError, read_table
from .report import CHECKS, Report, render_checks, render_json, render_text
from .rules import RulesError, apply_rules, load_rules

EXIT_OK = 0  # nothing to fix (warnings are allowed, unless --strict)
EXIT_FOUND = 1  # the audit found errors
EXIT_USAGE = 2  # the command or an input file could not be understood


def split(text: str | None) -> tuple[str, ...]:
    """'a, b' -> ('a', 'b'); nothing -> ()."""
    return tuple(part.strip() for part in text.split(",") if part.strip()) if text else ()


def delimiter_from(text: str | None) -> str | None:
    return "\t" if text in ("tab", "\\t") else text


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--delimiter", metavar="CHAR", help="what separates the cells: one character or 'tab' (default: detected)"
    )
    common.add_argument("--decimal", choices=[".", ","], help="the decimal separator of numbers (default: detected)")
    common.add_argument("--encoding", help="the text encoding (default: UTF-8, then the Windows code page cp1252)")
    common.add_argument(
        "--missing", metavar="WORDS", help="comma-separated words that mean 'no value' (replaces the built-in list)"
    )
    common.add_argument(
        "--format", choices=["text", "json"], default="text", help="how to print the report (default: text)"
    )
    common.add_argument("--strict", action="store_true", help="warnings fail the run too, not only errors")

    parser = argparse.ArgumentParser(
        prog="table-audit",
        description="Look at a data table before you trust it. Exit code 0: nothing to fix; 1: errors; 2: bad input.",
    )
    parser.add_argument("--version", action="version", version=f"table-audit {__version__}")
    commands = parser.add_subparsers(dest="command", required=True, metavar="{profile,check,leakage,explain}")

    one = commands.add_parser(
        "profile", parents=[common], help="describe a table and report what looks wrong, with no rules"
    )
    one.add_argument("file", type=Path)
    one.add_argument("--key", metavar="COLUMNS", help="columns that must be unique together (comma-separated)")

    rules = commands.add_parser("check", parents=[common], help="check a table against rules written in a JSON file")
    rules.add_argument("file", type=Path)
    rules.add_argument("--rules", required=True, type=Path, metavar="FILE", help="the rules (see the README)")

    leak = commands.add_parser(
        "leakage", parents=[common], help="look for leakage between a training file and a test file"
    )
    leak.add_argument("train", type=Path)
    leak.add_argument("test", type=Path)
    leak.add_argument(
        "--group", metavar="COLUMNS", help="who or what each row is about, such as subject_id (comma-separated)"
    )
    leak.add_argument("--target", metavar="COLUMN", help="the column the model must predict")
    leak.add_argument("--time", metavar="COLUMN", help="a date column, when the split must respect the order of time")
    leak.add_argument(
        "--ignore", metavar="COLUMNS", help="columns left out of the comparison of rows (comma-separated)"
    )

    explain = commands.add_parser("explain", help="list what the checks look for, or explain one of them")
    explain.add_argument("name", nargs="?", help="a check, as it is named in a report (default: list them all)")
    return parser


def run(args: argparse.Namespace) -> Report:
    def load(path: Path) -> Table:
        return read_table(
            path,
            delimiter=delimiter_from(args.delimiter),
            decimal=args.decimal,
            encoding=args.encoding,
            missing=split(args.missing) if args.missing is not None else None,
        )

    if args.command == "profile":
        return profile(load(args.file), split(args.key))
    if args.command == "check":
        rules = load_rules(args.rules)
        table = load(args.file)
        report = Report("check", [table.name], [*facts(table), ("rules", args.rules.name)])
        report.findings = [*audit_structure(table), *apply_rules(table, rules)]
        return report
    train, test = load(args.train), load(args.test)
    options = Options(split(args.group), args.target, args.time, split(args.ignore))
    return audit_leakage(train, test, options)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:  # a Windows console in a legacy code page must not crash on an accent
            reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if args.command == "explain":
        if args.name is not None and args.name not in CHECKS:
            close = difflib.get_close_matches(args.name, CHECKS, n=1)
            hint = f"; did you mean {close[0]!r}?" if close else "; `table-audit explain` lists them"
            print(f"table-audit: error: there is no check called {args.name!r}{hint}", file=sys.stderr)
            return EXIT_USAGE
        sys.stdout.write(render_checks(__version__, args.name))
        return EXIT_OK
    try:
        report = run(args)
    except (TableError, RulesError) as error:
        print(f"table-audit: error: {error}", file=sys.stderr)
        return EXIT_USAGE
    render = render_json if args.format == "json" else render_text
    sys.stdout.write(render(report, __version__, args.strict))
    return EXIT_FOUND if report.failed(args.strict) else EXIT_OK
