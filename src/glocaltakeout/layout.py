"""Folder and filename rules compatible with a gphotos-sync library.

gphotos-sync stores one real file per item under ``photos/YYYY/MM/`` and
names a second file that shares the original upload name ``name (n).ext``.
Album folders live under ``albums/YYYY/`` as ``MM Album title``. This module
reimplements that naming. It does not copy gphotos-sync source code.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

PHOTOS_DIR = "photos"
ALBUMS_DIR = "albums"

# "name (1).ext" — a space, then a parenthesized count, immediately before the suffix.
_DUPLICATE_SUFFIX = re.compile(r"^(?P<stem>.*?) \((?P<number>[1-9][0-9]*)\)$")


def photo_relative_dir(taken: datetime) -> Path:
    """Return ``photos/YYYY/MM`` for the capture time."""
    return Path(PHOTOS_DIR) / f"{taken.year:04d}" / f"{taken.month:02d}"


def split_duplicate_name(filename: str) -> tuple[str, int]:
    """Split ``name (n).ext`` into the original filename and the duplicate number.

    A name without the suffix has duplicate number 0.
    """
    path = Path(filename)
    match = _DUPLICATE_SUFFIX.match(path.stem)
    if match is None:
        return filename, 0
    original = f"{match.group('stem')}{path.suffix}"
    return original, int(match.group("number"))


def duplicate_filename(original: str, number: int) -> str:
    """Return ``original`` when ``number`` is 0, otherwise ``stem (n).ext``."""
    if number <= 0:
        return original
    path = Path(original)
    return f"{path.stem} ({number}){path.suffix}"


def _fold(name: str, case_insensitive: bool) -> str:
    return name.casefold() if case_insensitive else name


def next_duplicate_filename(
    original: str,
    existing_names: list[str] | tuple[str, ...],
    *,
    case_insensitive: bool = False,
) -> tuple[str, int]:
    """Pick the next free ``name (n).ext`` among names already in one folder.

    The original name uses number 0. The next file uses one more than the
    highest number already stored for that original name.
    """
    wanted = _fold(original, case_insensitive)
    highest: int | None = None
    for name in existing_names:
        current_original, number = split_duplicate_name(name)
        if _fold(current_original, case_insensitive) == wanted:
            highest = number if highest is None else max(highest, number)
    if highest is None:
        return original, 0
    number = highest + 1
    return duplicate_filename(original, number), number


def album_relative_dir(title: str, newest: datetime) -> Path:
    """Return ``albums/YYYY/MM Title`` using the album's newest capture time."""
    leaf = f"{newest.month:02d} {title.strip()}"
    return Path(ALBUMS_DIR) / f"{newest.year:04d}" / leaf


def folder_matches_album(folder_name: str, title: str) -> bool:
    """True when a leaf folder is the album title, or ``MM Title`` from gphotos-sync."""
    cleaned = title.strip()
    if not cleaned:
        return False
    if folder_name == cleaned:
        return True
    return folder_name.endswith(f" {cleaned}")
