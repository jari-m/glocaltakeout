import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

from glocaltakeout.index import DestinationIndex, GphotosHint, hash_file
from glocaltakeout.match import place_item
from glocaltakeout.takeout import ArchiveMember, TakeoutItem


def _item(filename: str, payload: bytes, taken: datetime, digest: str) -> TakeoutItem:
    return TakeoutItem(
        filename=filename,
        sha256=digest,
        size=len(payload),
        taken=taken,
        description="",
        latitude=None,
        longitude=None,
        altitude=None,
        source=ArchiveMember(Path("."), filename, len(payload)),
    )


def _library(root: Path, files: dict[str, bytes]) -> None:
    for relative, payload in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def test_identical_bytes_are_skipped(tmp_path: Path):
    payload = b"same-bytes"
    _library(tmp_path, {"photos/2020/05/IMG.jpg": payload})
    index = DestinationIndex(tmp_path)
    index.ensure_hashes_for_sizes({len(payload)})
    item = _item("IMG.jpg", payload, datetime(2020, 5, 2, 10, 0), hash_file(tmp_path / "photos/2020/05/IMG.jpg"))
    placement = place_item(item, index)
    assert placement.action == "skip_identical"
    assert placement.relative_path.as_posix() == "photos/2020/05/IMG.jpg"


def test_same_capture_different_bytes_keeps_both(tmp_path: Path):
    taken = datetime(2020, 5, 2, 10, 0)
    _library(tmp_path, {"photos/2020/05/IMG.jpg": b"api-copy"})
    database = tmp_path / "gphotos.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE SyncFiles (Path TEXT, FileName TEXT, OrigFileName TEXT, CreateDate TEXT)"
    )
    connection.execute(
        "INSERT INTO SyncFiles VALUES (?, ?, ?, ?)",
        ("photos/2020/05", "IMG.jpg", "IMG.jpg", "2020-05-02 10:00:00"),
    )
    connection.commit()
    connection.close()
    index = DestinationIndex(tmp_path, GphotosHint(database))
    index.ensure_hashes_for_sizes({8})
    item = _item("IMG.jpg", b"takeout-original-bytes", taken, "f" * 64)
    placement = place_item(item, index)
    assert placement.action == "keep_both"
    assert placement.relative_path.as_posix() == "photos/2020/05/IMG (1).jpg"
    assert placement.reason == "same capture, different bytes"


def test_case_insensitive_name_match(tmp_path: Path):
    taken = datetime(2020, 5, 2, 10, 0)
    _library(tmp_path, {"photos/2020/05/img.jpg": b"api-copy"})
    database = tmp_path / "gphotos.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE SyncFiles (Path TEXT, FileName TEXT, OrigFileName TEXT, CreateDate TEXT)"
    )
    connection.execute(
        "INSERT INTO SyncFiles VALUES ('photos/2020/05', 'img.jpg', 'img.jpg', '2020-05-02 10:00:00')"
    )
    connection.commit()
    connection.close()
    index = DestinationIndex(tmp_path, GphotosHint(database))
    item = _item("IMG.jpg", b"other", taken, "a" * 64)
    placement = place_item(item, index, case_insensitive=True)
    assert placement.action == "keep_both"
    assert placement.relative_path.name == "IMG (1).jpg"


def test_nfc_option_matches_a_decomposed_filename(tmp_path: Path):
    composed = "café.jpg"
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    _library(tmp_path, {f"photos/2024/06/{decomposed}": b"already-there"})
    index = DestinationIndex(tmp_path)
    item = _item(composed, b"takeout-bytes", datetime(2024, 6, 1, 12, 0), "c" * 64)
    placement = place_item(item, index, normalize_nfc=True)
    assert placement.relative_path.name == "café (1).jpg"
    assert unicodedata.is_normalized("NFC", placement.relative_path.name)


def test_unrelated_filename_clash_is_reported_separately(tmp_path: Path):
    _library(tmp_path, {"photos/2024/01/IMG.jpg": b"older"})
    database = tmp_path / "gphotos.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE SyncFiles (Path TEXT, FileName TEXT, OrigFileName TEXT, CreateDate TEXT)"
    )
    connection.execute(
        "INSERT INTO SyncFiles VALUES ('photos/2024/01', 'IMG.jpg', 'IMG.jpg', '2024-01-01 08:00:00')"
    )
    connection.commit()
    connection.close()
    index = DestinationIndex(tmp_path, GphotosHint(database))
    item = _item("IMG.jpg", b"newer", datetime(2024, 1, 20, 8, 0), "b" * 64)
    placement = place_item(item, index)
    assert placement.action == "name_clash"
    assert placement.relative_path.as_posix() == "photos/2024/01/IMG (1).jpg"
