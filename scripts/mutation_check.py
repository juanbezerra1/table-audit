"""Break the project in one small way at a time and check that the tests notice.

    py scripts/mutation_check.py                the mutations, one by one, with the verdict of each (about a minute)
    py scripts/mutation_check.py fold decimal   only the mutations whose name contains one of these words
    py scripts/mutation_check.py --list         the names, and nothing else

It needs only what the tests need (pytest). The project is copied to a temporary folder and the copy is the one that
gets broken, so your files are never touched, even if you press Ctrl+C.

A test-suite that passes proves little until you have seen it fail. Each mutation below is a plausible mistake (a
threshold moved, a comparison turned around, a strip forgotten). The tests named next to it must fail. If one does
not, the script says SURVIVED and exits with 1: either the tests miss something, or the mutation is harmless and
should go.

tests/test_docs.py fails when the line a mutation targets is no longer in the code exactly once, so a refactor cannot
quietly turn this check into a no-op.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = Path("src") / "table_audit"


@dataclass(frozen=True)
class Mutation:
    name: str
    file: str  # inside src/table_audit
    old: str  # must appear exactly once in the file
    new: str
    tests: tuple[str, ...]  # arguments for pytest: the files, and perhaps -k "..."


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "a column is numbers at 99 percent, not 90",
        "profile.py",
        "MIXED_SHARE = 0.9 ",
        "MIXED_SHARE = 0.99 ",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "outliers need 600 deviations, not 6",
        "profile.py",
        "OUTLIER_Z = 6.0 ",
        "OUTLIER_Z = 600.0 ",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "missing is a warning from 60 percent, not 50",
        "profile.py",
        "MANY_MISSING = 0.5 ",
        "MANY_MISSING = 0.6 ",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "placeholders are looked for in five numbers, not eight",
        "profile.py",
        "MIN_FOR_SENTINELS = 8\n",
        "MIN_FOR_SENTINELS = 5\n",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "every -999 is a placeholder, even in a column of -999s",
        "profile.py",
        "if v in SENTINELS and far(v)]",
        "if v in SENTINELS]",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "empty rows are repeated rows",
        "profile.py",
        "continue  # an empty row is not a repeated one; audit_structure reports it",
        "pass",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "duplicate rows are compared with their spaces",
        "profile.py",
        "signature = tuple(cell.strip() for cell in row)",
        "signature = tuple(row)",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "keys are compared with their spaces",
        "profile.py",
        "key = tuple(row[p].strip() for p in positions)",
        "key = tuple(row[p] for p in positions)",
        ("tests/test_profile.py",),
    ),
    Mutation(
        "fold keeps the accents",
        "values.py",
        'plain = "".join(c for c in decomposed if not unicodedata.combining(c))',
        "plain = decomposed",
        ("tests/test_values.py", "tests/test_profile.py"),
    ),
    Mutation(
        "infinity is a number",
        "values.py",
        "return value if math.isfinite(value) else None",
        "return value",
        ("tests/test_values.py",),
    ),
    Mutation(
        "a tie between decimal commas and points goes to the comma",
        "reader.py",
        'return "," if commas > dots else "."',
        'return "," if commas >= dots else "."',
        ("tests/test_reader.py",),
    ),
    Mutation(
        "the Windows code page is read as latin-1",
        "reader.py",
        'text = data.decode("cp1252")',
        'text = data.decode("latin-1")',
        ("tests/test_reader.py",),
    ),
    Mutation(
        "the delimiter is always a comma",
        "reader.py",
        "chosen = delimiter or detect_delimiter(text)",
        'chosen = delimiter or ","',
        ("tests/test_reader.py",),
    ),
    Mutation(
        "short rows are not padded",
        "reader.py",
        'rows.append((record + [""] * len(columns))[: len(columns)])',
        "rows.append(record[: len(columns)])",
        ("tests/test_reader.py",),
    ),
    Mutation(
        "a pattern may match anywhere in the value",
        "rules.py",
        "pattern.fullmatch(text.strip())",
        "pattern.search(text.strip())",
        ("tests/test_rules.py",),
    ),
    Mutation(
        "the maximum is compared the wrong way round",
        "rules.py",
        "_less(rule.maximum, value)",
        "_less(value, rule.maximum)",
        ("tests/test_rules.py",),
    ),
    Mutation(
        "a subject only in the test file is a shared subject",
        "leakage.py",
        "        if k in seen:\n",
        "        if k not in seen:\n",
        ("tests/test_leakage.py",),
    ),
    Mutation(
        "the target is compared when looking for identical rows",
        "leakage.py",
        "excluded = {*options.ignore, *([options.target] if options.target else [])}",
        "excluded = {*options.ignore}",
        ("tests/test_leakage.py",),
    ),
    Mutation(
        "only a high score can separate the classes",
        "leakage.py",
        "if score is not None and max(score, 1 - score) >= PERFECT_AUC:",
        "if score is not None and score >= PERFECT_AUC:",
        ("tests/test_leakage.py",),
    ),
    Mutation(
        "a date column of different days is an identifier",
        "leakage.py",
        "is_date = sum(parse_date(f) is not None for f in values) >= 0.9 * len(values)",
        "is_date = False",
        ("tests/test_leakage.py",),
    ),
    Mutation(
        "a test that starts the day the training ends is out of order",
        "leakage.py",
        "if d < latest]",
        "if d <= latest]",
        ("tests/test_leakage.py",),
    ),
    Mutation(
        "warnings fail a run even without --strict",
        "report.py",
        'return counts["error"] > 0 or (strict and counts["warning"] > 0)',
        'return counts["error"] > 0 or counts["warning"] > 0',
        ("tests/test_report.py", "tests/test_cli.py"),
    ),
    Mutation(
        "--strict is left out of the exit code",
        "cli.py",
        "EXIT_FOUND if report.failed(args.strict) else EXIT_OK",
        "EXIT_FOUND if report.failed() else EXIT_OK",
        ("tests/test_cli.py",),
    ),
)


def problems_with_the_list(root: Path = ROOT) -> list[str]:
    """What is wrong with the list itself: a target that is missing or ambiguous."""
    found: list[str] = []
    names = [m.name for m in MUTATIONS]
    found.extend(f"the name {n!r} is used twice" for n in sorted(set(names)) if names.count(n) > 1)
    for m in MUTATIONS:
        path = root / PACKAGE / m.file
        count = path.read_text(encoding="utf-8").count(m.old) if path.exists() else 0
        if count != 1:
            found.append(f"{m.name}: the text to break appears {count} times in {m.file} (it must be exactly once)")
        if m.old == m.new:
            found.append(f"{m.name}: the mutation changes nothing")
        for argument in m.tests:
            if argument.startswith("tests/") and not (root / argument).exists():
                found.append(f"{m.name}: {argument} does not exist")
    return found


def run(copy: Path, mutation: Mutation, env: dict[str, str]) -> tuple[str, str]:
    """Break one thing in the copy, run its tests, put it back. Returns (verdict, what failed first)."""
    target = copy / PACKAGE / mutation.file
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace(mutation.old, mutation.new, 1), encoding="utf-8")
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-x", "-q", "-rf", "-p", "no:cacheprovider", *mutation.tests],
            cwd=copy,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        target.write_text(original, encoding="utf-8")
    first = re.search(r"^FAILED (\S+)", done.stdout, re.MULTILINE)
    if done.returncode == 1:
        return "caught", first.group(1).split("::", 1)[-1] if first else "?"
    if done.returncode == 0:
        return "SURVIVED", "every test passed"
    return "ERROR", f"pytest exited with {done.returncode}: {(done.stdout + done.stderr).strip()[-300:]}"


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    words = [a for a in arguments if not a.startswith("--")]
    flags = [a for a in arguments if a.startswith("--")]
    chosen = [m for m in MUTATIONS if not words or any(w.lower() in m.name.lower() for w in words)]
    if "--list" in flags:
        print("\n".join(m.name for m in chosen))
        return 0
    if not chosen:
        print("no mutation has that word in its name; try --list", file=sys.stderr)
        return 2
    broken = problems_with_the_list()
    if broken:
        print("the list of mutations no longer fits the code:\n  " + "\n  ".join(broken), file=sys.stderr)
        return 2

    survivors = 0
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="table-audit-mutation-") as tmp:
        copy = Path(tmp) / "project"
        shutil.copytree(
            ROOT,
            copy,
            ignore=shutil.ignore_patterns(
                ".git", "__pycache__", ".*_cache", "*.egg-info", "build", "dist", ".venv", "venv"
            ),
        )
        # The copy's own source comes first on the path, whatever way the package was installed.
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(copy / "src"), os.environ.get("PYTHONPATH", "")])}
        seen = subprocess.run(
            [sys.executable, "-c", "import table_audit; print(table_audit.__file__)"],
            cwd=copy,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not seen.startswith(str(copy)):
            print(f"the tests would not run on the copy ({seen}); is table_audit importable?", file=sys.stderr)
            return 2

        for number, mutation in enumerate(chosen, start=1):
            verdict, detail = run(copy, mutation, env)
            survivors += verdict != "caught"
            print(f"{number:2}/{len(chosen)}  {verdict:<8} {mutation.name}  ->  {detail}", flush=True)
    seconds = time.monotonic() - started
    print(f"\n{len(chosen) - survivors} of {len(chosen)} mutations were caught in {seconds:.0f} s.")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
