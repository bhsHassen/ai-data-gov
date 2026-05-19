"""
cobol_reverse — command-line entry point.

Usage:
    python run.py inspect [input_dir]

    input_dir defaults to ./input/raw
"""
from __future__ import annotations

import sys
from pathlib import Path

from src.cobol_reverse.inspect import (
    inspect_directory,
    print_summary,
    save_report,
)
from src.cobol_reverse.console import log


COMMANDS = ("inspect",)


def cmd_inspect(args: list[str]) -> int:
    input_dir = Path(args[0]) if args else Path("input/raw")
    log("inspector", f"scanning {input_dir.resolve()}")

    reports = inspect_directory(input_dir)
    print_summary(reports)
    save_report(reports, Path("output/inspect.json"))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__.strip())
        print(f"\nAvailable commands: {', '.join(COMMANDS)}")
        return 1

    command = argv[1]
    rest    = argv[2:]
    if command == "inspect":
        return cmd_inspect(rest)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
