import json
import sqlite3
from datetime import datetime
from pathlib import Path

import piexif

from glocaltakeout.albums import link_album_file
from glocaltakeout.metadata import apply_new_file_metadata, read_jpeg_date
from glocaltakeout.pipeline import run
from glocaltakeout.takeout import TakeoutItem

# 1x1 JPEG
JPEG = bytes.fromhex(
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


def test_jpeg_metadata_is_written_only_on_the_new_file(tmp_path: Path):
    existing = tmp_path / "old.jpg"
    existing.write_bytes(JPEG)
    before = existing.read_bytes()
    new_file = tmp_path / "new.jpg"
    new_file.write_bytes(JPEG)
    item = TakeoutItem(
        filename="new.jpg",
        sha256="abc",
        size=len(JPEG),
        taken=datetime(2024, 6, 1, 15, 30, 0),
        description="harbor",
        latitude=60.17,
        longitude=24.94,
        altitude=5.0,
        source=None,  # type: ignore[arg-type]
    )
    apply_new_file_metadata(new_file, item)
    assert existing.read_bytes() == before
    assert read_jpeg_date(new_file) == datetime(2024, 6, 1, 15, 30, 0)
    loaded = piexif.load(str(new_file))
    assert loaded["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
    assert new_file.stat().st_mtime == datetime(2024, 6, 1, 15, 30, 0).timestamp()


def test_non_jpeg_gets_a_sidecar(tmp_path: Path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not-a-real-video")
    item = TakeoutItem(
        filename="clip.mp4",
        sha256="abc",
        size=16,
        taken=datetime(2024, 6, 1, 15, 30, 0),
        description="dock",
        latitude=None,
        longitude=None,
        altitude=None,
        source=None,  # type: ignore[arg-type]
    )
    apply_new_file_metadata(video, item)
    sidecar = json.loads((tmp_path / "clip.mp4.json").read_text(encoding="utf-8"))
    assert sidecar["description"] == "dock"
    assert sidecar["photoTakenTime"] == "2024-06-01T15:30:00"


def test_scan_counts_files_beyond_gphotos_syncfiles(tmp_path: Path):
    """SyncFiles can be shorter than photos/ after deletions from Google."""
    library = tmp_path / "library"
    folder = library / "photos" / "2020" / "01"
    folder.mkdir(parents=True)
    for number in range(101):
        (folder / f"img{number}.png").write_bytes(b"x")
    database = library / "gphotos.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE SyncFiles (Path TEXT, FileName TEXT, OrigFileName TEXT, CreateDate TEXT)"
    )
    connection.execute(
        "INSERT INTO SyncFiles VALUES ('photos/2020/01', 'img0.png', 'img0.png', '2020-01-01 00:00:00')"
    )
    connection.commit()
    connection.close()
    notes: list[tuple[int, int, str, str]] = []

    def progress(step: int, steps: int, label: str, detail: str) -> None:
        notes.append((step, steps, label, detail))

    list(
        run(
            [],
            library,
            apply=False,
            report_path=tmp_path / "report.json",
            progress=progress,
        )
    )
    details = [detail for step, _steps, label, detail in notes if label == "Scanning library"]
    assert details.index("0 files") > details.index("100%")
    assert details.index("100 files") > details.index("0 files")


def test_dry_run_does_not_copy(tmp_path: Path):
    archive = tmp_path / "takeout.zip"
    library = tmp_path / "library"
    (library / "photos" / "2020" / "01").mkdir(parents=True)
    (library / "photos" / "2020" / "01" / "already.png").write_bytes(b"png")
    import zipfile

    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("Takeout/Google Photos/Photos from 2024/a.jpg", JPEG)
        handle.writestr(
            "Takeout/Google Photos/Photos from 2024/a.jpg.supplemental-metadata.json",
            json.dumps(
                {
                    "title": "a.jpg",
                    "photoTakenTime": {"timestamp": "1717250000"},
                    "geoData": {"latitude": 1.5, "longitude": 2.5, "altitude": 0},
                    "description": "pier",
                }
            ),
        )
    report = tmp_path / "report.json"
    notes: list[tuple[int, int, str, str]] = []

    def progress(step: int, steps: int, label: str, detail: str) -> None:
        notes.append((step, steps, label, detail))

    events = list(run([archive], library, apply=False, report_path=report, progress=progress))
    assert [item[0] for item in notes if item[2] == "Saving report and index"][-1:] == [6]
    assert all(item[1] == 6 for item in notes)
    assert any(item[3] == "1 files" for item in notes)
    assert any(event.kind == "decided" for event in events)
    assert not (library / "photos").exists() or not any((library / "photos").rglob("*.jpg"))
    body = json.loads(report.read_text(encoding="utf-8"))
    assert body["apply"] is False
    assert body["decisions"][0]["action"] == "new_file"
    assert (library / "glocaltakeout.sqlite").exists()


def test_apply_copies_and_second_run_skips(tmp_path: Path):
    archive = tmp_path / "takeout.zip"
    library = tmp_path / "library"
    (library / "photos").mkdir(parents=True)
    import zipfile

    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("Takeout/Google Photos/Photos from 2024/a.jpg", JPEG)
        handle.writestr(
            "Takeout/Google Photos/Photos from 2024/a.jpg.json",
            json.dumps(
                {
                    "title": "a.jpg",
                    "description": "",
                    "photoTakenTime": {"timestamp": "1717250000"},
                    "geoData": {},
                }
            ),
        )
        handle.writestr(
            "Takeout/Google Photos/Holiday/a.jpg",
            JPEG,
        )
        handle.writestr(
            "Takeout/Google Photos/Holiday/metadata.json",
            json.dumps({"albumData": {"title": "Holiday"}}),
        )
    report = tmp_path / "report.json"
    list(run([archive], library, apply=True, report_path=report))
    copied = list((library / "photos").rglob("*.jpg"))
    assert len(copied) == 1
    album_links = [path for path in (library / "albums").rglob("*") if path.is_symlink()]
    assert len(album_links) == 1
    assert album_links[0].resolve() == copied[0].resolve()
    second = tmp_path / "report-2.json"
    list(run([archive], library, apply=True, report_path=second))
    body = json.loads(second.read_text(encoding="utf-8"))
    assert body["decisions"][0]["action"] == "skip_identical"
    assert len(list((library / "photos").rglob("*.jpg"))) == 1


def test_pending_list_when_symlink_fails(tmp_path: Path, monkeypatch):
    library = tmp_path / "library"
    target = library / "photos" / "2024" / "06" / "a.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(JPEG)

    def fail_symlink(self, target):  # type: ignore[no-untyped-def]
        raise OSError("symlinks are not supported")

    monkeypatch.setattr(Path, "symlink_to", fail_symlink)
    link = link_album_file(library, "Holiday", datetime(2024, 6, 1), target, apply=True)
    assert link.pending is True
