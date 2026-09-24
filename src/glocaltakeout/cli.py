"""Command line wrapper. Printing stays here so a later GUI can call ``run``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from glocaltakeout.pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge a Google Takeout photo export into a gphotos-sync library."
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=Path,
        help="Takeout zip parts or an extracted Takeout directory",
    )
    parser.add_argument(
        "--library",
        required=True,
        type=Path,
        help="Library root that contains photos/ and albums/",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy files and update albums. Without this flag the run only reports.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Where to write the JSON report (default: ./glocaltakeout-report.json)",
    )
    parser.add_argument(
        "--gphotos-db",
        type=Path,
        default=None,
        help="Path to gphotos.sqlite. Defaults to the library root.",
    )
    parser.add_argument(
        "--case-insensitive",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Compare destination filenames without case. Default: on for Windows.",
    )
    parser.add_argument(
        "--normalize-filenames",
        choices=("nfc",),
        default=None,
        help="Compare and write filenames in Unicode NFC. Use this on macOS. Default: off.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for event in run(
        args.sources,
        args.library,
        apply=args.apply,
        report_path=args.report,
        gphotos_db=args.gphotos_db,
        case_insensitive=args.case_insensitive,
        normalize_filenames=args.normalize_filenames,
    ):
        print(f"{event.kind}: {event.message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
