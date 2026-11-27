"""The command: exit codes, the two formats, the options, and what is printed when the input is wrong."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from table_audit import __version__
from table_audit.cli import EXIT_FOUND, EXIT_OK, EXIT_USAGE, main

from .helpers import EXAMPLES, ROOT

CLEAN = "id;x\nA;1,5\nB;2,5\nC;3,5\n"
ONLY_A_WARNING = "a,b\n1,2\n,\n3,4\n"  # an empty row: a warning, no error


def run(capsys: pytest.CaptureFixture[str], *argv: object) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def write(tmp_path: Path, name: str, text: str | bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return path


# --- exit codes ----------------------------------------------------------------------------------------------------


def test_a_clean_table_exits_with_zero_and_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "profile", write(tmp_path, "clean.csv", CLEAN))
    assert (code, err) == (EXIT_OK, "")
    assert "findings: none" in out and out.rstrip().endswith("result: ok")


def test_errors_exit_with_one(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "profile", EXAMPLES / "participants.csv", "--key", "subject_id,visit")
    assert code == EXIT_FOUND
    assert out.rstrip().endswith("result: FAILED (2 errors)")


def test_warnings_pass_unless_the_run_is_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "warn.csv", ONLY_A_WARNING)
    code, out, _ = run(capsys, "profile", path)
    assert code == EXIT_OK and out.rstrip().endswith("result: ok (1 warning)")
    code, out, _ = run(capsys, "profile", path, "--strict")
    assert code == EXIT_FOUND and out.rstrip().endswith("result: FAILED (1 warning)")


def test_strict_does_not_count_information(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "blank.csv", "a,b\n1,2\n\n3,4\n")  # a blank line: information only
    code, out, _ = run(capsys, "profile", path, "--strict")
    assert code == EXIT_OK and "info    blank-lines" in out


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["profile", "nothing-here.csv"], "cannot read nothing-here.csv"),
        (["check", "nothing-here.csv", "--rules", str(EXAMPLES / "rules.json")], "cannot read nothing-here.csv"),
        (["check", str(EXAMPLES / "participants.csv"), "--rules", "nope.json"], "cannot read nope.json"),
        (["profile", str(EXAMPLES / "participants.csv"), "--key", "nope"], "has no column 'nope'"),
        (["leakage", str(EXAMPLES / "train.csv"), str(EXAMPLES / "test.csv"), "--group", "nope"], "--group 'nope'"),
        (["profile", str(EXAMPLES / "rules.json"), "--delimiter", "ab"], "the delimiter must be one character"),
        (["profile", str(EXAMPLES / "train.csv"), "--encoding", "nonsense"], "cannot read the file as nonsense"),
    ],
)
def test_input_that_cannot_be_used_exits_with_two_and_says_why_on_stderr(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, argv: list[str], message: str
) -> None:
    monkeypatch.chdir(ROOT)
    code, out, err = run(capsys, *argv)
    assert code == EXIT_USAGE and out == ""
    assert err.startswith("table-audit: error: ") and message in err and err.count("\n") == 1


def test_a_mistake_in_the_rules_file_exits_with_two_and_names_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rules = write(tmp_path, "mine.json", '{"columns": {"age": {"maximum": 5}}}')
    code, _, err = run(capsys, "check", EXAMPLES / "participants.csv", "--rules", rules)
    assert code == EXIT_USAGE
    assert err == "table-audit: error: mine.json: columns.age: unknown key 'maximum'; did you mean 'max'?\n"


def test_a_wrong_command_line_is_argparses_exit_code_two(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in ([], ["profile"], ["check", "x.csv"], ["leakage", "a.csv"], ["profile", "x.csv", "--decimal", ";"]):
        with pytest.raises(SystemExit) as stop:
            main(argv)
        assert stop.value.code == 2
    assert "usage: table-audit" in capsys.readouterr().err


def test_version_prints_the_version_of_the_package(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out == f"table-audit {__version__}\n"


# --- the options ---------------------------------------------------------------------------------------------------


def test_the_delimiter_the_decimal_separator_and_the_encoding_can_be_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, "odd.csv", "a;b\tc\n1;2\t3\n".encode("cp1252"))
    for given in ("tab", "\\t"):
        _, out, _ = run(capsys, "profile", path, "--delimiter", given, "--encoding", "cp1252", "--decimal", ".")
        assert "delimiter tab, decimal '.', encoding cp1252" in out and "shape   1 rows x 2 columns" in out
        assert "(detected)" not in out.split("columns")[0]


def test_the_words_for_missing_can_be_replaced(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = "\n".join(f"{i};{i}" for i in range(1, 30)) + "\n30;ND\n"
    path = write(tmp_path, "nd.csv", "id;x\n" + body)
    _, out, _ = run(capsys, "profile", path)
    assert "mixed-types" in out
    code, out, _ = run(capsys, "profile", path, "--missing", "ND, SD")
    assert code == EXIT_OK and "findings: none" in out


def test_a_file_with_windows_line_endings_and_a_byte_order_mark_gives_the_same_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = (EXAMPLES / "participants.csv").read_bytes().decode("utf-8")
    plain = write(tmp_path, "same.csv", text.replace("\r\n", "\n"))
    windows = write(tmp_path, "same2.csv", b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode())
    first = run(capsys, "profile", plain)[1]
    second = run(capsys, "profile", windows)[1]
    assert first.replace("same.csv", "X") == second.replace("same2.csv", "X")


def test_leakage_takes_lists_of_columns_with_commas_and_spaces(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        capsys,
        "leakage",
        EXAMPLES / "train.csv",
        EXAMPLES / "test.csv",
        "--group",
        "subject_id, session",
        "--ignore",
        "date, site",
    )
    assert code == EXIT_FOUND and "subject_id+session values" in out


# --- the report ------------------------------------------------------------------------------------------------------


def test_the_json_report_is_complete_and_sorted_with_errors_first(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        capsys, "profile", EXAMPLES / "participants.csv", "--key", "subject_id,visit", "--format", "json"
    )
    report = json.loads(out)
    assert code == EXIT_FOUND
    assert report["tool"] == "table-audit" and report["version"] == __version__ and report["command"] == "profile"
    assert report["sources"] == ["participants.csv"]
    assert report["facts"]["shape"] == "36 rows x 6 columns"
    assert [c["name"] for c in report["columns"]][:2] == ["subject_id", "visit"]
    severities = [f["severity"] for f in report["findings"]]
    assert severities == sorted(severities, key=["error", "warning", "info"].index)
    assert report["summary"] == {"error": 2, "warning": 8, "info": 1, "failed": True}
    first = report["findings"][0]
    assert set(first) == {"check", "severity", "column", "message", "count", "lines", "examples"}


def test_the_json_report_of_a_clean_table_says_it_did_not_fail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "profile", write(tmp_path, "clean.csv", CLEAN), "--format", "json")
    assert code == EXIT_OK
    assert json.loads(out)["summary"] == {"error": 0, "warning": 0, "info": 0, "failed": False}


def test_strict_changes_the_json_verdict_too(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write(tmp_path, "warn.csv", ONLY_A_WARNING)
    assert json.loads(run(capsys, "profile", path, "--format", "json")[1])["summary"]["failed"] is False
    assert json.loads(run(capsys, "profile", path, "--format", "json", "--strict")[1])["summary"]["failed"] is True


def test_the_json_of_a_leakage_report_names_both_files(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(capsys, "leakage", EXAMPLES / "train.csv", EXAMPLES / "test.csv", "--format", "json")
    report = json.loads(out)
    assert report["sources"] == ["train.csv", "test.csv"] and report["command"] == "leakage"


@pytest.mark.parametrize(
    "argv",
    [
        ["profile", "participants.csv", "--key", "subject_id,visit"],
        ["check", "participants.csv", "--rules", "rules.json"],
        ["leakage", "train.csv", "test.csv", "--group", "subject_id", "--target", "qc", "--time", "date"],
    ],
)
def test_the_text_report_is_plain_ascii_so_that_a_windows_console_can_show_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    monkeypatch.chdir(EXAMPLES)
    _, out, _ = run(capsys, *argv)
    assert out.isascii(), [c for c in out if not c.isascii()]


# --- as a program ------------------------------------------------------------------------------------------------------


def program(*argv: object, **env: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-m", "table_audit", *map(str, argv)],
        capture_output=True,
        cwd=ROOT,
        env={**os.environ, **env},
        check=False,
    )


def test_it_runs_as_a_module_with_the_same_exit_code_and_report(capsys: pytest.CaptureFixture[str]) -> None:
    done = program("profile", "examples/participants.csv")
    code, out, _ = run(capsys, "profile", ROOT / "examples" / "participants.csv")
    assert done.returncode == code == EXIT_FOUND
    assert done.stdout.decode("ascii").replace("\r\n", "\n") == out and done.stderr == b""  # Windows ends lines in CRLF


def test_a_value_the_console_cannot_show_is_replaced_and_does_not_crash_the_report(tmp_path: Path) -> None:
    rows = ["name"] + ["Não"] * 5 + ["nao"] * 3 + ["Sim"] * 6
    path = write(tmp_path, "accents.csv", "\n".join(rows) + "\n")
    done = program("profile", path, PYTHONIOENCODING="ascii", PYTHONUTF8="0")
    assert done.returncode == EXIT_FOUND or done.returncode == EXIT_OK
    assert b"Traceback" not in done.stderr
    assert b"label-variants" in done.stdout and b"N?o" in done.stdout


def test_a_file_in_the_windows_code_page_is_read_and_the_report_says_so(tmp_path: Path) -> None:
    path = write(tmp_path, "excel.csv", "nome;idade\nJoão;30\nAçucena;41\n".encode("cp1252"))
    done = program("profile", path)
    assert done.returncode == EXIT_OK
    assert b"encoding cp1252" in done.stdout and b"was read as cp1252" in done.stdout


# --- explain -----------------------------------------------------------------------------------------------------------


def test_explain_lists_the_checks_and_explains_one(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "explain")
    assert (code, err) == (EXIT_OK, "") and "label-variants" in out and "time-order" in out and out.isascii()
    code, out, err = run(capsys, "explain", "label-variants")
    assert (code, err) == (EXIT_OK, "")
    assert out.splitlines()[0] == f"table-audit {__version__}: explain label-variants"
    assert "one label written in more than one way" in out


def test_explain_of_a_name_that_is_no_check_suggests_the_closest(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = run(capsys, "explain", "placeholdr")
    assert code == EXIT_USAGE and out == ""
    assert err == "table-audit: error: there is no check called 'placeholdr'; did you mean 'placeholder'?\n"
    code, _, err = run(capsys, "explain", "zzzzzz")
    assert code == EXIT_USAGE and "`table-audit explain` lists them" in err
