import json
import zipfile
from datetime import datetime
from pathlib import Path

from glocaltakeout.takeout import (
    expand_takeout_parts,
    is_year_bucket,
    load_takeout,
    match_sidecar_name,
)


def _sidecar(title: str, taken: int, **extra: object) -> bytes:
    payload = {
        "title": title,
        "description": extra.pop("description", ""),
        "photoTakenTime": {"timestamp": str(taken)},
        "geoData": extra.pop(
            "geoData",
            {"latitude": 60.1, "longitude": 24.9, "altitude": 12.0},
        ),
    }
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def _zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)


def test_year_bucket_ignores_language_and_keeps_titled_albums():
    assert is_year_bucket("Vuosi 2026", has_album_metadata=False)
    assert is_year_bucket("Photos from 2019", has_album_metadata=False)
    assert not is_year_bucket("asuntomessut 2026 ja remontti-ideat", has_album_metadata=True)
    assert not is_year_bucket("Photos from 2019", has_album_metadata=True)


def test_sidecar_names_cover_supplemental_numbered_and_edited():
    names = [
        "IMG.jpg.supplemental-metadata.json",
        "shot.jpg.json",
        "clip.mp4.supplemental-metadata(1).json",
    ]
    assert match_sidecar_name("IMG.jpg", names) == "IMG.jpg.supplemental-metadata.json"
    assert match_sidecar_name("shot.jpg", names) == "shot.jpg.json"
    assert match_sidecar_name("clip(1).mp4", names) == "clip.mp4.supplemental-metadata(1).json"
    assert match_sidecar_name("shot-edited.jpg", names) == "shot.jpg.json"


def test_truncated_supplemental_name_matches_long_filename():
    media = "a" * 40 + ".jpg"
    # Google shortens "supplemental-metadata" so the sidecar name stays short.
    shortened = media + ".s.json"
    # A very long stem can also be cut off before ".json".
    stem_cut = "holiday-photo-with-a-very-long-na.json"
    long_media = "holiday-photo-with-a-very-long-name.jpg"
    assert match_sidecar_name(media, [shortened]) == shortened
    assert match_sidecar_name(long_media, [stem_cut]) == stem_cut


def test_first_zip_part_includes_numbered_siblings(tmp_path: Path):
    names = [
        "takeout-20260924T153744Z-1-001.zip",
        "takeout-20260924T153744Z-1-002.zip",
        "takeout-20260924T153744Z-1-005.zip",
        "other-export-1-001.zip",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"")
    expanded = expand_takeout_parts([tmp_path / names[0]])
    assert [path.name for path in expanded] == names[:3]


def test_finnish_layout_classifies_folders_and_dedupes_album_copy(tmp_path: Path):
    photo = b"year-bytes"
    only_album = b"album-bytes"
    archived = b"archived-bytes"
    trashed = b"trashed-bytes"
    part_one = tmp_path / "takeout-1.zip"
    part_two = tmp_path / "takeout-2.zip"
    _zip(
        part_one,
        {
            "Takeout/Google Kuvat/Vuosi 2026/IMG.jpg": photo,
            "Takeout/Google Kuvat/Vuosi 2026/IMG.jpg.supplemental-metadata.json": _sidecar(
                "IMG.jpg", 1_700_000_000, description="dock"
            ),
            "Takeout/Google Kuvat/Arkistoi/old.png": archived,
            "Takeout/Google Kuvat/Arkistoi/old.png.supplemental-metadata.json": _sidecar(
                "old.png", 1_600_000_000, archived=True, geoData={}
            ),
            "Takeout/Google Kuvat/Roskakori/gone.jpg": trashed,
            "Takeout/Google Kuvat/Roskakori/gone.jpg.supplemental-metadata.json": _sidecar(
                "gone.jpg", 1_500_000_000, trashed=True, geoData={}
            ),
            "Takeout/archive_browser.html": b"<html></html>",
        },
    )
    _zip(
        part_two,
        {
            "Takeout/Google Kuvat/asuntomessut 2026 ja remontti-ideat/IMG.jpg": photo,
            "Takeout/Google Kuvat/asuntomessut 2026 ja remontti-ideat/IMG.jpg.supplemental-metadata.json": _sidecar(
                "IMG.jpg", 1_700_000_000
            ),
            "Takeout/Google Kuvat/asuntomessut 2026 ja remontti-ideat/extra.jpg": only_album,
            "Takeout/Google Kuvat/asuntomessut 2026 ja remontti-ideat/metadata.json": json.dumps(
                {"albumData": {"title": "Housing fair"}}
            ).encode("utf-8"),
        },
    )

    items = {item.filename: item for item in load_takeout([part_one, part_two])}
    assert set(items) == {"IMG.jpg", "old.png", "extra.jpg"}
    assert items["IMG.jpg"].albums == ["Housing fair"]
    assert items["IMG.jpg"].description == "dock"
    assert items["IMG.jpg"].taken == datetime(2023, 11, 14, 22, 13, 20)
    assert items["IMG.jpg"].latitude == 60.1
    assert items["old.png"].archived is True
    assert items["old.png"].albums == []
    assert "gone.jpg" not in items
