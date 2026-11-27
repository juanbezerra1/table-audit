"""The documentation says what the code does.

A README that has drifted from the code is worse than none, so these tests read the documents the way a stranger
would: they follow every link, count what the README counts, compare its tables with the code and run every command
that it shows, comparing what it prints with what the README says it prints.

When one fails because a block is out of date, the message says what to run.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from urllib.parse import unquote

import pytest

from table_audit.cli import EXIT_FOUND, EXIT_OK, EXIT_USAGE, build_parser, main
from table_audit.report import CHECKS
from table_audit.rules import COLUMN_KEYS, TOP_KEYS, load_rules
from table_audit.values import DEFAULT_MISSING

from .helpers import EXAMPLES, ROOT, load_script

README = ROOT / "README.md"
README_PT = ROOT / "README.pt-BR.md"
MARKDOWN = [README, README_PT]

FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.S | re.M)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")

# Things that live in the repository, as they are written in the docs. A code span that starts with one of these
# directories (or is one of these files) has to exist.
DIRECTORIES = {"src", "tests", "examples", "scripts", ".github"}
FILES = {"README.md", "README.pt-BR.md", "LICENSE", "pyproject.toml"}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def blocks(markdown: str) -> list[tuple[str, str]]:
    """(the text after the opening fence, the body) of every fenced block."""
    return [(m.group(1).strip(), m.group(2)) for m in FENCE.finditer(markdown)]


def prose(markdown: str) -> str:
    return FENCE.sub("", markdown)


def headings(markdown: str) -> list[tuple[int, str]]:
    return [(len(m.group(1)), m.group(2)) for m in re.finditer(r"^(#{1,6}) +(.+?) *$", prose(markdown), re.M)]


def slug(heading: str) -> str:
    """The anchor GitHub makes from a heading: lower case, punctuation gone, spaces become hyphens."""
    text = heading.replace("`", "").strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def tables(markdown: str) -> list[list[list[str]]]:
    """Every table of the prose as a list of rows of cells (the separator row left out)."""
    found: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in [*prose(markdown).splitlines(), ""]:
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                current.append(cells)
        elif current:
            found.append(current)
            current = []
    return found


def table_with(markdown: str, first_cell: str) -> list[list[str]]:
    """The rows (without the header) of the table whose first row starts with this cell."""
    for table in tables(markdown):
        if len(table) > 1 and table[1][0] == first_cell:
            return table[1:]
    raise AssertionError(f"no table whose first row starts with {first_cell}")


# ---------------------------------------------------------------------------------------------------------------
# the two READMEs


def test_the_two_readmes_are_twins() -> None:
    english, portuguese = read(README), read(README_PT)
    # What a reader types or reads as output is the same in both languages; only the prose is translated.
    assert blocks(english) == blocks(portuguese)
    assert len(blocks(english)) >= 12
    assert [level for level, _ in headings(english)] == [level for level, _ in headings(portuguese)]

    def local(markdown: str) -> list[str]:  # each one links to the other, so those two are left out
        links = LINK.findall(prose(markdown))
        return [
            t for t in links if not t.startswith(("http", "#", "mailto")) and t not in (README.name, README_PT.name)
        ]

    assert local(english) == local(portuguese)
    english_tables, portuguese_tables = tables(english), tables(portuguese)
    assert [len(t) for t in english_tables] == [len(t) for t in portuguese_tables]
    # the first column of a table that names things (checks, keys, modules, paths) names the same things
    for a, b in zip(english_tables, portuguese_tables, strict=True):
        assert [CODE.findall(row[0]) for row in a[1:]] == [CODE.findall(row[0]) for row in b[1:]]


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_every_link_and_every_path_in_the_docs_exists(path: Path) -> None:
    text = read(path)
    problems: list[str] = []
    anchors = {slug(h) for _, h in headings(text)}
    for target in LINK.findall(prose(text)):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target):  # https:, mailto:
            continue
        where, _, anchor = target.partition("#")
        destination = (path.parent / where).resolve() if where else path
        if not destination.exists():
            problems.append(f"the link {target} points to nothing")
        elif anchor and destination.suffix == ".md":
            wanted = {slug(h) for _, h in headings(read(destination))} if where else anchors
            if unquote(anchor) not in wanted:
                problems.append(f"the link {target} points to a heading that does not exist")
    for span in CODE.findall(prose(text)):
        if re.search(r"[\s<>{}*|$=:]|\.\.\.", span) or span.startswith(("/", "-", "http")):
            continue
        if (span.split("/")[0] in DIRECTORIES or span in FILES) and not (ROOT / span).exists():
            problems.append(f"`{span}` is named but is not in the repository")
    assert not problems, f"{path.name}:\n  " + "\n  ".join(problems)


def defined_tests() -> set[str]:
    return {name for p in (ROOT / "tests").glob("test_*.py") for name in re.findall(r"^def (test_\w+)", read(p), re.M)}


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_tests_named_in_the_docs_exist(path: Path) -> None:
    named = {re.sub(r"\[.*\]$", "", span) for span in CODE.findall(prose(read(path))) if span.startswith("test_")}
    missing = sorted(name for name in named if name not in defined_tests() and not name.endswith(".py"))
    assert not missing, f"{path.name} names tests that do not exist: {missing}"


# ---------------------------------------------------------------------------------------------------------------
# the numbers and the lists


def commands_of_the_program() -> list[str]:
    usage = build_parser().format_usage()
    found = re.search(r"\{([a-z,]+)\}", usage)
    assert found is not None
    return found.group(1).split(",")


def number_of_dependencies() -> int:
    declared = re.search(r"^dependencies = \[(.*?)\]", read(ROOT / "pyproject.toml"), re.M | re.S)
    assert declared is not None
    return len([part for part in declared.group(1).split(",") if part.strip()])


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_numbers_in_the_readmes_are_the_numbers_of_the_code(path: Path) -> None:
    mutations = len(load_script("mutation_check").MUTATIONS)
    text = read(path)
    glance = [int(row[1]) for row in tables(text)[0][1:]]
    assert glance == [
        len(commands_of_the_program()),
        len(CHECKS),
        len(COLUMN_KEYS),
        number_of_dependencies(),
        mutations,
    ]
    said = [int(n) for n in re.findall(r"\b(\d+) (?:plausible ways|jeitos plausíveis)\b", text)]
    assert said == [mutations], (
        f"{path.name}: the prose says {said} ways of breaking the program, there are {mutations}"
    )


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_table_of_checks_is_the_list_the_program_keeps(path: Path) -> None:
    rows = table_with(read(path), "`encoding`")
    assert [row[0].strip("`") for row in rows] == list(CHECKS)
    for row in rows:
        name = row[0].strip("`")
        assert row[1] == f"`{CHECKS[name].severity}`", name
        assert row[2] == ", ".join(f"`{c}`" for c in CHECKS[name].commands.split(", ")), name
        if path == README:
            assert row[3] == CHECKS[name].meaning, name


def code_names_in_the_code() -> set[str]:
    """The names of checks that the source files give to a Finding, found in the text of the source."""
    names: set[str] = set()
    for source in (ROOT / "src" / "table_audit").glob("*.py"):
        text = read(source)
        names |= set(re.findall(r'Finding\(\s*"([a-z-]+)"', text))
        names |= set(re.findall(r'check: str = "([a-z-]+)"', text))
        names |= set(re.findall(r'not_of_the_type\([^)]*?"([a-z-]+)", "error"\)', text))
    return names


def test_every_check_that_the_source_creates_is_in_the_list_and_the_other_way_round() -> None:
    created = code_names_in_the_code()
    assert created <= set(CHECKS), f"findings of checks that CHECKS does not list: {sorted(created - set(CHECKS))}"
    tests = "".join(read(p) for p in (ROOT / "tests").glob("test_*.py") if p.name != "test_docs.py")
    unseen = sorted(name for name in CHECKS if f'"{name}"' not in tests)
    assert not unseen, f"checks that no test names: {unseen}"


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_tables_of_keys_and_exit_codes_say_what_the_program_does(path: Path) -> None:
    text = read(path)
    keys = [row[0].strip("`") for row in table_with(text, "`type`")]
    assert keys == list(COLUMN_KEYS)
    for top in TOP_KEYS:  # in a code span, or as a key in the JSON of the rules
        assert f"`{top}`" in text or f"`{top}:" in text or f'"{top}"' in text, f"the key {top} is not explained"
    codes = [row[0].strip("`") for row in table_with(text, "`0`")]
    assert codes == [str(EXIT_OK), str(EXIT_FOUND), str(EXIT_USAGE)]


# ---------------------------------------------------------------------------------------------------------------
# what the README runs and shows


def console_blocks(markdown: str) -> list[tuple[list[str], str]]:
    """(the arguments after `table-audit`, the rest of the block) of every console block."""
    found: list[tuple[list[str], str]] = []
    for info, body in blocks(markdown):
        if info == "console":
            first, _, rest = body.partition("\n")
            assert first.startswith("$ table-audit "), f"a console block must start with the command: {first!r}"
            found.append((shlex.split(first.removeprefix("$ table-audit ")), rest.rstrip("\n")))
    return found


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_every_console_block_is_what_the_program_prints(
    path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(ROOT)
    shown = console_blocks(read(path))
    assert {argv[0] for argv, _ in shown} == set(commands_of_the_program())
    for argv, expected in shown:
        main(argv)
        captured = capsys.readouterr()
        actual = (captured.out + captured.err).rstrip("\n")
        assert actual == expected, (
            f"the block of `table-audit {' '.join(argv)}` in {path.name} is out of date; "
            "run the command and paste what it prints (the generator that wrote it is not part of the repository)"
        )


def real_report(*argv: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    monkeypatch.chdir(ROOT)
    main([*argv, "--format", "json"])
    report: dict[str, object] = json.loads(capsys.readouterr().out)
    return report


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_json_finding_in_the_readme_is_what_the_program_prints(
    path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = [json.loads(body) for info, body in blocks(read(path)) if info == "json" and '"check"' in body]
    assert len(shown) == 1
    report = real_report(
        "profile", "examples/participants.csv", "--key", "subject_id,visit", monkeypatch=monkeypatch, capsys=capsys
    )
    findings = report["findings"]
    assert isinstance(findings, list) and shown[0] in findings
    assert set(shown[0]) == {"check", "severity", "column", "message", "count", "lines", "examples"}


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_rules_in_the_readme_are_the_rules_of_the_examples(path: Path) -> None:
    shown = [json.loads(body) for info, body in blocks(read(path)) if info == "json" and '"columns"' in body]
    assert shown == [json.loads(read(EXAMPLES / "rules.json"))]
    assert load_rules(EXAMPLES / "rules.json").columns  # and they are rules the program accepts


def options_in(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", text))


def test_every_option_the_readme_names_is_one_the_program_has(capsys: pytest.CaptureFixture[str]) -> None:
    known: set[str] = set()
    for command in ("", *commands_of_the_program()):
        with pytest.raises(SystemExit):
            main([*([command] if command else []), "--help"])
        known |= options_in(capsys.readouterr().out)
    other_programs = {"--check"}  # `ruff format --check`
    for path in MARKDOWN:
        unknown = options_in(read(path)) - known - other_programs
        assert not unknown, f"{path.name} names options that table-audit does not have: {sorted(unknown)}"
    assert {"--key", "--rules", "--group", "--target", "--time", "--ignore", "--strict", "--format"} <= known


def test_every_command_the_readme_names_is_a_command_of_the_program() -> None:
    for path in MARKDOWN:
        text = read(path)
        spans = [s for s in CODE.findall(prose(text)) if s.startswith("table-audit ")]
        spans += [body for info, body in blocks(text) if info in ("yaml", "bash")]
        words = {w for s in spans for w in re.findall(r"table-audit ([a-z][a-z-]*)", s)}
        assert words <= set(commands_of_the_program()), f"{path.name}: {sorted(words - set(commands_of_the_program()))}"
        assert {"explain", "check"} <= words


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: p.name)
def test_the_words_for_missing_the_readme_lists_are_the_words_the_reader_uses(path: Path) -> None:
    rows = [r for t in tables(read(path)) for r in t if r[0] in ("Missing", "Ausente")]
    assert len(rows) == 1
    listed = {span.casefold() for span in CODE.findall(rows[0][1])}
    assert listed == {word for word in DEFAULT_MISSING if word}


# ---------------------------------------------------------------------------------------------------------------
# the examples that the README says are dirty on purpose


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        (README, r"(\w+) synthetic tables and (\w+) rules files"),
        (README_PT, r"(\w+) tabelas sintéticas e (\w+) arquivos de regras"),
    ],
    ids=lambda x: x.name if isinstance(x, Path) else "",
)
def test_the_examples_are_what_the_readme_says_they_are(path: Path, pattern: str) -> None:
    names = sorted(p.name for p in EXAMPLES.iterdir())
    assert names == ["participants.csv", "rules-with-a-mistake.json", "rules.json", "test.csv", "train.csv"]
    words = {"dois": 2, "três": 3, "two": 2, "three": 3, "four": 4}
    found = re.search(pattern, read(path))
    assert found is not None, f"{path.name} does not say how many tables and rules files the examples have"
    assert [words[w] for w in found.groups()] == [
        len([n for n in names if n.endswith(".csv")]),
        len([n for n in names if n.endswith(".json")]),
    ]


# ---------------------------------------------------------------------------------------------------------------
# hygiene


def test_no_file_of_the_repository_carries_an_email_address() -> None:
    skip = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}
    pattern = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
    allowed = {"users.noreply.github.com", "github.com"}  # GitHub's address for people who do not show theirs, and git@
    found: list[str] = []

    def invented(domain: str) -> bool:
        # *.example is reserved for examples (RFC 2606)
        return domain.endswith(".example")

    for path in ROOT.rglob("*"):
        if not path.is_file() or skip & set(path.relative_to(ROOT).parts) or path.suffix in {".pyc", ".whl", ".zip"}:
            continue
        for address in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
            domain = address.split("@", 1)[1]
            if domain not in allowed and not invented(domain):
                found.append(f"{path.relative_to(ROOT)}: {address}")
    assert not found, "an email address must not be published:\n  " + "\n  ".join(found)
