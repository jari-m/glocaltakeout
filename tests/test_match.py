import sqlite3
import unicodedata
from datetime import datetime
from pathlib import Path

import piexif

from glocaltakeout.index import DestinationIndex, GphotosHint, hash_file
from glocaltakeout.match import place_item
from glocaltakeout.takeout import ArchiveMember, TakeoutItem

# 1x1 JPEG, same bytes the metadata tests use.
_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb00430008060607060508070707090908"
    "0a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c303134"
    "34341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101"
    "010100000000000000000102030405060708090a0bffc400b510000201030302040305050404"
    "0000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433"
    "627282090a161718191a25262728292a3435363738393a434445464748494a53545556575859"
    "5a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5"
    "a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6"
    "e7e8e9eaf1f2f3f4f5f6f7f8f9faffda00080001000100003f00fbffd9"
)


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


def _sync_db(path: Path, rows: list[tuple[str, str, str, str]]) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE SyncFiles (Path TEXT, FileName TEXT, OrigFileName TEXT, CreateDate TEXT)"
    )
    connection.executemany("INSERT INTO SyncFiles VALUES (?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()


def test_previous_database_fills_a_capture_time_the_current_one_lacks(tmp_path: Path):
    taken = datetime(2020, 5, 2, 10, 0)
    _library(tmp_path, {"photos/2020/05/IMG.jpg": b"api-copy"})
    _sync_db(tmp_path / "gphotos.sqlite", [])
    _sync_db(
        tmp_path / "gphotos.sqlite.previous",
        [("photos/2020/05", "IMG.jpg", "IMG.jpg", "2020-05-02 10:00:00")],
    )
    index = DestinationIndex(tmp_path, GphotosHint(tmp_path / "gphotos.sqlite"))
    item = _item("IMG.jpg", b"takeout-original-bytes", taken, "f" * 64)
    placement = place_item(item, index)
    assert placement.action == "keep_both"
    assert placement.relative_path.as_posix() == "photos/2020/05/IMG (1).jpg"


def test_current_database_wins_over_previous(tmp_path: Path):
    _library(tmp_path, {"photos/2020/05/IMG.jpg": b"api-copy"})
    _sync_db(
        tmp_path / "gphotos.sqlite",
        [("photos/2020/05", "IMG.jpg", "IMG.jpg", "2024-01-01 08:00:00")],
    )
    _sync_db(
        tmp_path / "gphotos.sqlite.previous",
        [("photos/2020/05", "IMG.jpg", "IMG.jpg", "2020-05-02 10:00:00")],
    )
    index = DestinationIndex(tmp_path, GphotosHint(tmp_path / "gphotos.sqlite"))
    item = _item("IMG.jpg", b"newer", datetime(2020, 5, 2, 10, 0), "b" * 64)
    placement = place_item(item, index)
    assert placement.action == "name_clash"


def test_jpeg_date_is_used_when_both_databases_miss_the_file(tmp_path: Path):
    taken = datetime(2020, 5, 2, 10, 0)
    path = tmp_path / "photos" / "2020" / "05" / "IMG.jpg"
    path.parent.mkdir(parents=True)
    path.write_bytes(_JPEG)
    piexif.insert(
        piexif.dump({"Exif": {piexif.ExifIFD.DateTimeOriginal: b"2020:05:02 10:00:00"}}),
        str(path),
    )
    _sync_db(tmp_path / "gphotos.sqlite", [])
    index = DestinationIndex(tmp_path, GphotosHint(tmp_path / "gphotos.sqlite"))
    item = _item("IMG.jpg", b"takeout-original-bytes", taken, "e" * 64)
    placement = place_item(item, index)
    assert placement.action == "keep_both"


def test_reused_index_fills_a_missing_time_without_rescanning(tmp_path: Path):
    _library(tmp_path, {"photos/2020/05/IMG.jpg": b"api-copy"})
    first = DestinationIndex(tmp_path)
    first.connection.execute("UPDATE files SET captured = NULL")
    first.connection.commit()
    first.publish()
    _sync_db(
        tmp_path / "gphotos.sqlite.previous",
        [("photos/2020/05", "IMG.jpg", "IMG.jpg", "2020-05-02 10:00:00")],
    )
    notes: list[tuple[int, int]] = []
    second = DestinationIndex(
        tmp_path,
        GphotosHint(tmp_path / "gphotos.sqlite"),
        on_dates=lambda current, total: notes.append((current, total)),
    )
    assert second.rebuilt is False
    assert second.files()[0].captured == datetime(2020, 5, 2, 10, 0)
    assert notes[0] == (0, 1)
    assert notes[-1] == (1, 1)


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
