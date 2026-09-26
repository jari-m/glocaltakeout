"""Run a Takeout merge. Library code yields progress events and does not print."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

TOTAL_STEPS = 6
ProgressFn = Callable[[int, int, str, str], None]

from glocaltakeout.albums import AlbumLink, link_album_file, write_pending
from glocaltakeout.index import DestinationIndex, GphotosHint, IndexedFile, hash_file
from glocaltakeout.layout import canonical_name, split_duplicate_name
from glocaltakeout.match import Placement, place_item
from glocaltakeout.metadata import apply_new_file_metadata
from glocaltakeout.takeout import TakeoutItem, expand_takeout_parts, load_takeout


@dataclass(frozen=True)
class Progress:
    kind: str
    message: str


@dataclass
class RunResult:
    decisions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    report_path: Path | None = None
    index_rebuilt: bool = False


def _copy_member(item: TakeoutItem, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".partial")
    with item.source.open() as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target)
    temporary.replace(destination)


def _remember(index: DestinationIndex, item: TakeoutItem, placement: Placement) -> None:
    original, number = split_duplicate_name(placement.relative_path.name)
    index.remember(
        IndexedFile(
            relative_path=placement.relative_path,
            filename=placement.relative_path.name,
            original_name=original,
            duplicate_no=number,
            size=item.size,
            captured=item.taken,
            sha256=item.sha256,
            source_sha256=item.sha256,
        )
    )


def run(
    sources: list[Path],
    library_root: Path,
    *,
    apply: bool = False,
    report_path: Path | None = None,
    gphotos_db: Path | None = None,
    case_insensitive: bool | None = None,
    normalize_filenames: str | None = None,
    progress: ProgressFn | None = None,
) -> Iterator[Progress]:
    """Index Takeout and the library, then copy and link when ``apply`` is set.

    A dry run still publishes a refreshed ``glocaltakeout.sqlite``. It does not
    copy media or change ``albums/``.
    """
    if case_insensitive is None:
        case_insensitive = os.name == "nt"
    normalize_nfc = normalize_filenames == "nfc"

    shown: dict[tuple[int, str], str] = {}

    def note(
        step: int,
        label: str,
        current: int,
        total: int | None,
        *,
        as_count: bool = False,
        every: int = 1,
    ) -> None:
        if progress is None:
            return
        if as_count and total is None:
            if every > 1 and current not in (0, 1) and current % every != 0:
                return
            text = f"{current} files"
        elif total is None or total <= 0:
            text = "0%" if current == 0 else "100%"
        elif current >= total:
            text = "100%"
        else:
            text = f"{min(99, current * 100 // total)}%"
        key = (step, label)
        if shown.get(key) == text:
            return
        shown[key] = text
        progress(step, TOTAL_STEPS, label, text)

    sources = expand_takeout_parts(sources)
    note(1, "Reading Takeout", 0, None)

    def on_takeout_hash(current: int, total: int) -> None:
        note(1, "Reading Takeout", current, total)

    items = load_takeout(sources, on_hash=on_takeout_hash)
    note(1, "Reading Takeout", len(items), len(items))
    yield Progress("scanned", f"Indexed {len(items)} unique Takeout files")

    hint_path = gphotos_db if gphotos_db is not None else library_root / "gphotos.sqlite"
    # Pass the path even when the file is missing, so a sibling .previous
    # database can still supply capture times.
    hint = GphotosHint(hint_path)
    scanned = 0
    # SyncFiles only lists photos still known to the last gphotos-sync run.
    # Photos removed from Google stay under photos/ and are missing from that
    # count, so past the count we report how many further files have been seen.
    scan_estimate = hint.file_count

    counting = scan_estimate is None

    def on_scan(seen: int) -> None:
        nonlocal scanned
        scanned = seen
        if scan_estimate is None:
            note(2, "Scanning library", seen, None, as_count=True, every=50)
            return
        if seen <= scan_estimate:
            note(2, "Scanning library", seen, scan_estimate)
            return
        extra = seen - scan_estimate
        if extra != 1 and extra % 100 != 0:
            return
        note(2, "Scanning library", (extra // 100) * 100, None, as_count=True)

    dated = False

    def on_dates(current: int, total: int) -> None:
        nonlocal dated
        dated = True
        note(2, "Reading capture times", current, total)

    note(2, "Scanning library", 0, scan_estimate, as_count=counting)
    index = DestinationIndex(library_root, hint, on_scan=on_scan, on_dates=on_dates)
    if scanned:
        note(2, "Scanning library", scanned, scanned)
    elif not dated:
        note(2, "Scanning library", 1, 1)
    note(3, "Hashing library files", 0, None)

    def on_library_hash(current: int, total: int) -> None:
        note(3, "Hashing library files", current, total)

    hashed = index.ensure_hashes_for_sizes({item.size for item in items}, on_hash=on_library_hash)
    note(3, "Hashing library files", hashed, hashed)

    phantom: list[str] = []
    pending: list[AlbumLink] = []
    decisions: list[dict] = []
    warnings: list[str] = []
    warned_symlink = False
    warned_mtime = False

    placements: list[tuple[TakeoutItem, Placement]] = []
    item_total = len(items)
    match_label = "Copying files" if apply else "Matching files"
    note(4, match_label, 0, item_total)
    for number, item in enumerate(items, start=1):
        if normalize_nfc:
            item.filename = canonical_name(item.filename, normalize_nfc=True)
            item.albums = [canonical_name(title, normalize_nfc=True) for title in item.albums]
        placement = place_item(
            item, index, case_insensitive=case_insensitive, normalize_nfc=normalize_nfc
        )
        placements.append((item, placement))
        record = {
            "filename": item.filename,
            "action": placement.action,
            "path": placement.relative_path.as_posix(),
            "reason": placement.reason,
            "albums": list(item.albums),
        }
        decisions.append(record)
        yield Progress("decided", f"{placement.action}: {placement.relative_path.as_posix()}")

        if placement.action != "skip_identical":
            _remember(index, item, placement)
            if not apply:
                phantom.append(placement.relative_path.as_posix())
            else:
                destination = library_root / placement.relative_path
                _copy_member(item, destination)
                stamped = apply_new_file_metadata(destination, item)
                if stamped is False and not warned_mtime:
                    message = (
                        "This drive rejected file timestamps; capture times are in the file metadata"
                    )
                    warnings.append(message)
                    warned_mtime = True
                    yield Progress("warning", message)
                stored = hash_file(destination)
                index.connection.execute(
                    "UPDATE files SET sha256 = ?, size = ? WHERE relative_path = ?",
                    (stored, destination.stat().st_size, placement.relative_path.as_posix()),
                )
                index.connection.commit()
                yield Progress("copied", destination.as_posix())
        note(4, match_label, number, item_total)

    link_total = sum(len(item.albums) for item, _placement in placements)
    note(5, "Linking albums", 0, link_total)
    linked = 0
    for item, placement in placements:
        target = library_root / placement.relative_path
        newest = item.taken or datetime(1970, 1, 1)
        for title in item.albums:
            linked += 1
            link = link_album_file(
                library_root, title, newest, target, apply=apply, normalize_nfc=normalize_nfc
            )
            if link.pending:
                pending.append(link)
                if not warned_symlink:
                    message = "This drive cannot create symlinks; links were listed in albums-pending.json"
                    warnings.append(message)
                    warned_symlink = True
                    yield Progress("warning", message)
            elif apply and link.created:
                yield Progress("linked", link.relative_link.as_posix())
            note(5, "Linking albums", linked, link_total)

    if apply and pending:
        write_pending(library_root, pending)

    if phantom:
        for relative in phantom:
            index.connection.execute("DELETE FROM files WHERE relative_path = ?", (relative,))
        index.connection.commit()

    report = {
        "apply": apply,
        "decisions": decisions,
        "warnings": warnings,
        "pending_albums": [item.relative_link.as_posix() for item in pending],
    }
    destination_report = report_path or Path("glocaltakeout-report.json")
    note(6, "Saving report and index", 0, 2)
    destination_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    note(6, "Saving report and index", 1, 2)
    index.publish()
    note(6, "Saving report and index", 2, 2)
    yield Progress("scanned", f"Wrote report {destination_report}")
    return RunResult(decisions, warnings, destination_report, index.rebuilt)  # type: ignore[return-value]
