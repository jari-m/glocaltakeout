"""Run a Takeout merge. Library code yields progress events and does not print."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

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
) -> Iterator[Progress]:
    """Index Takeout and the library, then copy and link when ``apply`` is set.

    A dry run still publishes a refreshed ``glocaltakeout.sqlite``. It does not
    copy media or change ``albums/``.
    """
    if case_insensitive is None:
        case_insensitive = os.name == "nt"
    normalize_nfc = normalize_filenames == "nfc"
    sources = expand_takeout_parts(sources)
    if len(sources) > 1:
        yield Progress("scanned", f"Reading {len(sources)} Takeout parts")
    items = load_takeout(sources)
    yield Progress("scanned", f"Indexed {len(items)} unique Takeout files")

    hint_path = gphotos_db if gphotos_db is not None else library_root / "gphotos.sqlite"
    index = DestinationIndex(library_root, GphotosHint(hint_path if hint_path.exists() else None))
    index.ensure_hashes_for_sizes({item.size for item in items})

    phantom: list[str] = []
    pending: list[AlbumLink] = []
    decisions: list[dict] = []
    warnings: list[str] = []
    warned_symlink = False

    for item in items:
        if normalize_nfc:
            item.filename = canonical_name(item.filename, normalize_nfc=True)
            item.albums = [canonical_name(title, normalize_nfc=True) for title in item.albums]
        placement = place_item(
            item, index, case_insensitive=case_insensitive, normalize_nfc=normalize_nfc
        )
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
                apply_new_file_metadata(destination, item)
                stored = hash_file(destination)
                index.connection.execute(
                    "UPDATE files SET sha256 = ?, size = ? WHERE relative_path = ?",
                    (stored, destination.stat().st_size, placement.relative_path.as_posix()),
                )
                index.connection.commit()
                yield Progress("copied", destination.as_posix())

        target = library_root / placement.relative_path
        newest = item.taken or datetime(1970, 1, 1)
        for title in item.albums:
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
    destination_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    index.publish()
    yield Progress("scanned", f"Wrote report {destination_report}")
    return RunResult(decisions, warnings, destination_report, index.rebuilt)  # type: ignore[return-value]
