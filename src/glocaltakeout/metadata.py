"""Restore Takeout metadata onto files this tool has just copied.

Existing library files are not opened for writing. JPEG gets EXIF. Other
types get a JSON sidecar, because writing those formats needs ExifTool.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import piexif

from glocaltakeout.takeout import TakeoutItem

_JPEG = {".jpg", ".jpeg"}


def _rational(value: float) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    sign_value = abs(value)
    degrees = int(sign_value)
    minutes_full = (sign_value - degrees) * 60
    minutes = int(minutes_full)
    seconds = int(round((minutes_full - minutes) * 60 * 100))
    return ((degrees, 1), (minutes, 1), (seconds, 100))


def _exif_bytes(item: TakeoutItem) -> bytes:
    zeroth = {}
    exif = {}
    gps = {}
    if item.description:
        zeroth[piexif.ImageIFD.ImageDescription] = item.description.encode("utf-8")
    if item.taken is not None:
        text = item.taken.strftime("%Y:%m:%d %H:%M:%S")
        exif[piexif.ExifIFD.DateTimeOriginal] = text.encode("ascii")
        exif[piexif.ExifIFD.DateTimeDigitized] = text.encode("ascii")
        zeroth[piexif.ImageIFD.DateTime] = text.encode("ascii")
    if item.latitude is not None and item.longitude is not None:
        gps[piexif.GPSIFD.GPSLatitudeRef] = b"N" if item.latitude >= 0 else b"S"
        gps[piexif.GPSIFD.GPSLatitude] = _rational(item.latitude)
        gps[piexif.GPSIFD.GPSLongitudeRef] = b"E" if item.longitude >= 0 else b"W"
        gps[piexif.GPSIFD.GPSLongitude] = _rational(item.longitude)
        if item.altitude is not None:
            gps[piexif.GPSIFD.GPSAltitudeRef] = 0 if item.altitude >= 0 else 1
            gps[piexif.GPSIFD.GPSAltitude] = _rational(item.altitude)[0]
    return piexif.dump({"0th": zeroth, "Exif": exif, "GPS": gps})


def apply_new_file_metadata(path: Path, item: TakeoutItem) -> None:
    """Set mtime, and embed metadata, on a file this run created."""
    if path.suffix.lower() in _JPEG:
        piexif.insert(_exif_bytes(item), str(path))
    else:
        sidecar = path.with_name(path.name + ".json")
        payload = {
            "title": item.filename,
            "description": item.description,
            "photoTakenTime": None if item.taken is None else item.taken.strftime("%Y-%m-%dT%H:%M:%S"),
            "latitude": item.latitude,
            "longitude": item.longitude,
            "altitude": item.altitude,
        }
        sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if item.taken is not None:
        stamp = item.taken.timestamp()
        path.touch()
        import os

        os.utime(path, (stamp, stamp))


def read_jpeg_date(path: Path) -> datetime | None:
    """Return DateTimeOriginal from a JPEG, if piexif can read it."""
    try:
        loaded = piexif.load(str(path))
    except (piexif.InvalidImageDataError, ValueError):
        return None
    raw = loaded.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    try:
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
