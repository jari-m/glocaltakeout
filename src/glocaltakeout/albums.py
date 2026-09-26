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


def library_relative(path: Path, library_root: Path) -> Path:
    """Return ``path`` relative to the library root.

    A path that is already relative is kept. An absolute path from another
    machine is accepted when it still contains a ``photos/`` suffix, which is
    how ``albums-pending.json`` recorded targets before they were relative.
    """
    if not path.is_absolute():
        return Path(path.as_posix())
    try:
        return path.relative_to(library_root)
    except ValueError:
        parts = path.parts
        if "photos" in parts:
            return Path(*parts[parts.index("photos") :])
        raise ValueError(f"{path} is outside {library_root}") from None


def write_pending(library_root: Path, pending: list[AlbumLink]) -> Path | None:
    if not pending:
        return None
    path = library_root / "albums-pending.json"
    payload = [
        {
            "album": item.album,
            "link": item.relative_link.as_posix(),
            "target": library_relative(item.target, library_root).as_posix(),
        }
        for item in pending
    ]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _pending_from_object(item: dict, library_root: Path) -> tuple[str, Path, Path]:
    link = Path(item["link"])
    target = library_relative(Path(item["target"]), library_root)
    album = item.get("album") or link.parent.name
    return str(album), link, target


def links_from_report(body: dict, library_root: Path) -> list[tuple[str, Path, Path]]:
    """Read album links from a report, including the older list-of-paths form."""
    pending = body.get("pending_albums") or []
    if pending and isinstance(pending[0], dict):
        return [_pending_from_object(item, library_root) for item in pending]

    saved_links = [Path(item) for item in pending if isinstance(item, str)]
    entries: list[tuple[str, Path, Path]] = []
    for decision in body.get("decisions") or []:
        albums = decision.get("albums") or []
        if not albums or not decision.get("path"):
            continue
        target = Path(decision["path"])
        for title in albums:
            match = next(
                (
                    link
                    for link in saved_links
                    if link.name == target.name and folder_matches_album(link.parent.name, title)
                ),
                None,
            )
            if match is not None:
                saved_links.remove(match)
                entries.append((title, match, target))
                continue
            parts = target.parts
            if len(parts) >= 3 and parts[0] == "photos" and parts[1].isdigit() and parts[2].isdigit():
                newest = datetime(int(parts[1]), int(parts[2]), 1)
            else:
                newest = datetime(1970, 1, 1)
            entries.append((title, album_relative_dir(title, newest) / target.name, target))
    return entries


def load_saved_links(library_root: Path, report_path: Path | None = None) -> list[tuple[str, Path, Path]]:
    """Load links from a report, or from ``albums-pending.json`` in the library."""
    if report_path is not None:
        body = json.loads(report_path.read_text(encoding="utf-8"))
        return links_from_report(body, library_root)
    path = library_root / "albums-pending.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [_pending_from_object(item, library_root) for item in payload]


def create_saved_link(library_root: Path, link_relative: Path, target_relative: Path) -> bool:
    """Create one relative symlink. Return False when the drive rejects it."""
    link = library_root / link_relative
    target = library_root / target_relative
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        return True
    if link.exists():
        return False
    relative = Path(os.path.relpath(target, start=link.parent))
    try:
        link.symlink_to(relative)
    except OSError:
        return False
    return link.is_symlink()


def replay_album_links(
    library_root: Path,
    report_path: Path | None = None,
    on_link=None,
) -> tuple[int, list[AlbumLink]]:
    """Create symlinks from a previous report or ``albums-pending.json``.

    Does not read Takeout archives or copy photos. Successful links are removed
    from ``albums-pending.json``. Any that still fail are written back with a
    target relative to the library root.
    """
    entries = load_saved_links(library_root, report_path)
    total = len(entries)
    if on_link is not None:
        on_link(0, total)
    created = 0
    pending: list[AlbumLink] = []
    for number, (album, link_relative, target_relative) in enumerate(entries, start=1):
        if create_saved_link(library_root, link_relative, target_relative):
            created += 1
        else:
            pending.append(
                AlbumLink(album, link_relative, library_root / target_relative, created=False, pending=True)
            )
        if on_link is not None:
            on_link(number, total)
    pending_path = library_root / "albums-pending.json"
    if pending:
        write_pending(library_root, pending)
    elif entries and pending_path.exists():
        pending_path.unlink()
    return created, pending
