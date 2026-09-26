"""Command line wrapper. Printing stays here so a later GUI can call ``run``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from glocaltakeout.albums import replay_album_links
from glocaltakeout.pipeline import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Merge a Google Takeout photo export into a gphotos-sync library."
    )
    parser.add_argument(
        "sources",
        nargs="*",
        type=Path,
        help="Takeout zip parts or an extracted Takeout directory. Omit with --link-only.",
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
        "--link-only",
        action="store_true",
        help=(
            "Create album symlinks from albums-pending.json, or from --report, "
            "without reading Takeout archives."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Where to write the JSON report (default: ./glocaltakeout-report.json). "
            "With --link-only, this file is read and is not overwritten."
        ),
    )
    parser.add_argument(
        "--gphotos-db",
        type=Path,
        default=None,
        help="Path to gphotos.sqlite. Defaults to the library root.",
    )
    # store_true / store_false, not BooleanOptionalAction: that action needs Python 3.9.
    case_group = parser.add_mutually_exclusive_group()
    case_group.add_argument(
        "--case-insensitive",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Compare destination filenames without case. Default: on for Windows.",
    )
    case_group.add_argument(
        "--no-case-insensitive",
        action="store_false",
        dest="case_insensitive",
        default=argparse.SUPPRESS,
        help="Force a case-sensitive filename comparison.",
    )
    parser.add_argument(
        "--normalize-filenames",
        choices=("nfc",),
        default=None,
        help="Compare and write filenames in Unicode NFC. Use this on macOS. Default: off.",
    )
    return parser


def _link_only(args: argparse.Namespace) -> int:
    """Create album symlinks from a saved report or albums-pending.json."""

    def on_link(current: int, total: int) -> None:
        if total <= 0:
            detail = "0 files"
        elif current >= total:
            detail = "100%"
        else:
            detail = f"{min(99, current * 100 // total)}%"
        print(f"1/1 Linking albums: {detail}", flush=True)

    try:
        created, pending = replay_album_links(args.library, args.report, on_link=on_link)
    except (OSError, ValueError) as error:
        print(f"warning: {error}", flush=True)
        return 1
    if pending:
        print(
            f"warning: {len(pending)} album links could not be created; see albums-pending.json",
            flush=True,
        )
    print(f"Linked {created} album files", flush=True)
    return 0


def show_progress(step: int, steps: int, label: str, detail: str) -> None:
    """Print one progress line. ``detail`` is a percentage or a running file count."""
    print(f"{step}/{steps} {label}: {detail}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.link_only:
        if args.sources:
            parser.error("--link-only does not take Takeout sources")
        return _link_only(args)
    if not args.sources:
        parser.error("a Takeout source is required unless --link-only is set")
    for event in run(
        args.sources,
        args.library,
        apply=args.apply,
        report_path=args.report,
        gphotos_db=args.gphotos_db,
        case_insensitive=getattr(args, "case_insensitive", None),
        normalize_filenames=args.normalize_filenames,
        progress=show_progress,
    ):
        if event.kind == "warning":
            print(f"warning: {event.message}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
