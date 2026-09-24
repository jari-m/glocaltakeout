"""Read-only check against the local Takeout sample, when it is present."""

import json
import zipfile
from pathlib import Path

import pytest

from glocaltakeout.takeout import is_year_bucket

SAMPLE = Path("/mnt/c/TEMP/Incoming")


def test_sample_zip_roles_from_structure():
    if not SAMPLE.exists():
        pytest.skip("Takeout sample is not at /mnt/c/TEMP/Incoming")
    zips = sorted(SAMPLE.glob("takeout-*.zip"))
    assert len(zips) >= 1

    folders: dict[str, dict[str, int]] = {}
    for path in zips:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = info.filename.split("/")
                if len(parts) < 4 or parts[-1] == "archive_browser.html":
                    continue
                folder = parts[2]
                bucket = folders.setdefault(
                    folder, {"album_meta": 0, "archived": 0, "trashed": 0, "json": 0}
                )
                if parts[-1] == "metadata.json":
                    bucket["album_meta"] += 1
                elif parts[-1].lower().endswith(".json"):
                    bucket["json"] += 1
                    payload = json.loads(archive.read(info))
                    if isinstance(payload, dict):
                        bucket["archived"] += int(bool(payload.get("archived")))
                        bucket["trashed"] += int(bool(payload.get("trashed")))

    roles = {}
    for name, bucket in folders.items():
        if is_year_bucket(name, bucket["album_meta"] > 0):
            roles[name] = "year"
        elif bucket["json"] and bucket["trashed"] * 2 >= bucket["json"]:
            roles[name] = "trash"
        elif bucket["json"] and bucket["archived"] * 2 >= bucket["json"]:
            roles[name] = "archive"
        else:
            roles[name] = "album"

    assert roles["Vuosi 2026"] == "year"
    assert roles["Vuosi 2025"] == "year"
    assert roles["Arkistoi"] == "archive"
    assert roles["Roskakori"] == "trash"
    assert roles["asuntomessut 2026 ja remontti-ideat"] == "album"
