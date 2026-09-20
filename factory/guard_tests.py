#!/usr/bin/env python3

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ASSERT_RE = re.compile(
    r"""
    \bassert(?:\.|\s*\()
    |
    \bexpect\s*\(
    """,
    re.VERBOSE,
)

SUSPICIOUS_PATTERNS = {
    "skip/only/todo":
        re.compile(
            r"\b(?:test|it|describe)\.(?:skip|only|todo)\s*\("
        ),

    "skip/todo option":
        re.compile(
            r"\b(?:skip|todo)\s*:\s*true\b"
        ),

    "forced successful exit":
        re.compile(
            r"\bprocess\.exit\s*\(\s*0\s*\)"
        ),
}


def run_git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def read_base(repo, rel):
    p = run_git(
        repo,
        "show",
        f"HEAD:{rel}",
    )

    if p.returncode != 0:
        return ""

    return p.stdout


def count_assertions(text):
    return len(ASSERT_RE.findall(text))


def suspicious_counts(text):
    return {
        name: len(regex.findall(text))
        for name, regex
        in SUSPICIOUS_PATTERNS.items()
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)

    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    test_paths = [
        p.strip()
        for p in os.environ.get(
            "FACTORY_TEST_PATHS",
            "tests",
        ).split(",")
        if p.strip()
    ]

    changed_proc = run_git(
        repo,
        "diff",
        "--name-status",
        "HEAD",
        "--",
        *test_paths,
    )

    if changed_proc.returncode != 0:
        print(changed_proc.stderr)
        return 80

    untracked_proc = run_git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        *test_paths,
    )

    changed = {}

    for line in changed_proc.stdout.splitlines():
        if not line.strip():
            continue

        parts = line.split("\t")
        status = parts[0]

        if status.startswith("R") and len(parts) >= 3:
            changed[parts[2]] = "R"
        elif len(parts) >= 2:
            changed[parts[-1]] = status[0]

    for line in untracked_proc.stdout.splitlines():
        if line.strip():
            changed[line.strip()] = "A"

    errors = []

    for rel, status in sorted(changed.items()):
        path = repo / rel

        if status == "D":
            errors.append(
                f"{rel}: existing test file was deleted"
            )
            continue

        if not path.exists() or not path.is_file():
            continue

        current = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        baseline = (
            ""
            if status == "A"
            else read_base(repo, rel)
        )

        baseline_asserts = count_assertions(baseline)
        current_asserts = count_assertions(current)

        if (
            baseline_asserts > 0
            and current_asserts < baseline_asserts
        ):
            errors.append(
                f"{rel}: assertion count decreased "
                f"{baseline_asserts} -> {current_asserts}"
            )

        before = suspicious_counts(baseline)
        after = suspicious_counts(current)

        for name in SUSPICIOUS_PATTERNS:
            if after[name] > before[name]:
                errors.append(
                    f"{rel}: introduced suspicious "
                    f"test construct: {name}"
                )

    if errors:
        print("TEST_GUARD_FAIL")

        for error in errors:
            print("-", error)

        return 81

    if changed:
        print(
            "TEST_GUARD_OK:",
            ", ".join(sorted(changed)),
        )
    else:
        print("TEST_GUARD_OK: no test changes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
