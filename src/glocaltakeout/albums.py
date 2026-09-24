"""Create or reuse gphotos-sync album folders and point them at library files."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from glocaltakeout.layout import ALBUMS_DIR, album_relative_dir, folder_matches_album


@dataclass(frozen=True)
class AlbumLink:
    album: str
    relative_link: Path
    target: Path
    created: bool
    pending: bool


def find_album_dir(library_root: Path, title: str, *, normalize_nfc: bool = False) -> Path | None:
    """Return an existing album folder whose leaf name ends with ``title``."""
    root = library_root / ALBUMS_DIR
    if not root.exists():
        return None
    matches = [
        path
        for path in root.rglob("*")
        if path.is_dir() and folder_matches_album(path.name, title, normalize_nfc=normalize_nfc)
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda path: path.as_posix())[0]


def ensure_album_dir(
    library_root: Path, title: str, newest: datetime, *, normalize_nfc: bool = False
) -> Path:
    found = find_album_dir(library_root, title, normalize_nfc=normalize_nfc)
    if found is not None:
        return found
    created = library_root / album_relative_dir(title, newest)
    created.mkdir(parents=True, exist_ok=True)
    return created


def _relative_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    relative = Path(os.path.relpath(target, start=link.parent))
    if link.is_symlink() or link.exists():
        return
    link.symlink_to(relative)


def link_album_file(
    library_root: Path,
    title: str,
    newest: datetime,
    target: Path,
    *,
    apply: bool,
    normalize_nfc: bool = False,
) -> AlbumLink:
    """Add one relative symlink. ``target`` is absolute or under ``library_root``."""
    album_dir = (
        find_album_dir(library_root, title, normalize_nfc=normalize_nfc)
        if not apply
        else ensure_album_dir(library_root, title, newest, normalize_nfc=normalize_nfc)
    )
    if album_dir is None:
        album_dir = library_root / album_relative_dir(title, newest)
    link = album_dir / target.name
    relative_link = link.relative_to(library_root)
    if not apply:
        return AlbumLink(title, relative_link, target, created=False, pending=False)
    try:
        _relative_symlink(link, target)
    except OSError:
        return AlbumLink(title, relative_link, target, created=False, pending=True)
    return AlbumLink(title, relative_link, target, created=link.is_symlink(), pending=False)


def write_pending(library_root: Path, pending: list[AlbumLink]) -> Path | None:
    if not pending:
        return None
    path = library_root / "albums-pending.json"
    payload = [
        {
            "album": item.album,
            "link": item.relative_link.as_posix(),
            "target": item.target.as_posix()
            if item.target.is_absolute()
            else item.target.as_posix(),
        }
        for item in pending
    ]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
