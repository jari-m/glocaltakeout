"""Decide whether a Takeout item is already in the library."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from glocaltakeout.index import DestinationIndex, IndexedFile
from glocaltakeout.layout import (
    duplicate_filename,
    next_duplicate_filename,
    photo_relative_dir,
    split_duplicate_name,
)
from glocaltakeout.takeout import TakeoutItem

SAME_CAPTURE = timedelta(minutes=2)


@dataclass(frozen=True)
class Placement:
    """Where one Takeout item should go, relative to the library root."""

    action: str
    relative_path: Path
    reason: str


def _fold(name: str, case_insensitive: bool) -> str:
    return name.casefold() if case_insensitive else name


def _same_capture(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= SAME_CAPTURE


def _with_number(folder: Path, filename: str, number: int) -> Path:
    return folder / duplicate_filename(split_duplicate_name(filename)[0], number)


def place_item(
    item: TakeoutItem,
    index: DestinationIndex,
    *,
    case_insensitive: bool = False,
) -> Placement:
    """Choose skip, keep-both, or a new ``photos/YYYY/MM`` path."""
    existing = index.by_hash(item.sha256)
    if existing is not None:
        return Placement("skip_identical", existing.relative_path, "identical bytes")

    wanted = _fold(item.filename, case_insensitive)
    same_capture_files = [
        record
        for record in index.files()
        if _fold(record.original_name, case_insensitive) == wanted
        and _same_capture(record.captured, item.taken)
    ]
    if same_capture_files:
        folder = same_capture_files[0].relative_path.parent
        filename, _number = next_duplicate_filename(
            item.filename,
            index.names_in_dir(folder),
            case_insensitive=case_insensitive,
        )
        return Placement(
            "keep_both",
            folder / filename,
            "same capture, different bytes",
        )

    if item.taken is None:
        folder = Path("photos") / "undated"
    else:
        folder = photo_relative_dir(item.taken)
    filename, number = next_duplicate_filename(
        item.filename,
        index.names_in_dir(folder),
        case_insensitive=case_insensitive,
    )
    if number == 0:
        return Placement("new_file", folder / filename, "not in the library")
    clash = _conflicting_name(index, folder, item, case_insensitive)
    if clash:
        return Placement("name_clash", folder / filename, "different capture, same filename")
    return Placement("new_file", _with_number(folder, item.filename, number), "not in the library")


def _conflicting_name(
    index: DestinationIndex,
    folder: Path,
    item: TakeoutItem,
    case_insensitive: bool,
) -> IndexedFile | None:
    wanted = _fold(item.filename, case_insensitive)
    for record in index.files():
        if record.relative_path.parent != folder and record.relative_path.parent.as_posix() != folder.as_posix():
            continue
        if _fold(record.original_name, case_insensitive) == wanted:
            return record
    return None
