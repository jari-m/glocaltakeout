"""Read a Google Takeout photo export from zip parts or an extracted folder.

Folder roles come from the archive shape, not from translated names. See the
README for the year-bucket, archive, and trash rules.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator

MEDIA_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".3gp",
    ".mpg",
    ".mpeg",
}

_YEAR_AT_END = re.compile(r"^(?P<body>.*?)(?P<year>(?:19|20)\d{2})\s*$")
_EDITED_STEM = re.compile(r"^(?P<stem>.*)-edited$", re.IGNORECASE)
_NUMBER_BEFORE_EXT = re.compile(r"^(?P<stem>.*)\((?P<number>\d+)\)(?P<suffix>\.[^.]+)$")
_SUPPLEMENTAL_TAIL = re.compile(
    r"(?:\.supplemental-metadata|\.supplemental-metadat|\.supplemental-metada|"
    r"\.supplemental-metad|\.supplemental-meta|\.supplemental-met|\.supplemental-me|"
    r"\.supplemental-m|\.supplemental|\.suppleme|\.supplem|\.supple|\.suppl|\.supp|"
    r"\.sup|\.su|\.s)?(?:\(\d+\))?\.json$",
    re.IGNORECASE,
)


class _ClosingZipEntry(io.RawIOBase):
    """Zip member stream that also closes the archive."""

    def __init__(self, container: Path, member: str):
        self._archive = zipfile.ZipFile(container)
        self._handle = self._archive.open(member, "r")

    def read(self, size: int = -1) -> bytes:  # type: ignore[override]
        return self._handle.read(size)

    def close(self) -> None:
        try:
            self._handle.close()
        finally:
            self._archive.close()


@dataclass
class ArchiveMember:
    """One file inside a zip part or an extracted directory."""

    container: Path
    member: str
    size: int

    def open(self) -> BinaryIO:
        if zipfile.is_zipfile(self.container):
            return _ClosingZipEntry(self.container, self.member)
        path = self.container.joinpath(*PurePosixPath(self.member).parts)
        return path.open("rb")

    def read_bytes(self) -> bytes:
        with self.open() as handle:
            return handle.read()


@dataclass
class MediaOccurrence:
    """One media file as it appears in a single Takeout folder."""

    member: ArchiveMember
    folder: str
    filename: str
    role: str
    album_title: str | None = None
    metadata: dict | None = None


@dataclass
class TakeoutItem:
    """One unique media file after duplicates across folders are merged."""

    filename: str
    sha256: str
    size: int
    taken: datetime | None
    description: str
    latitude: float | None
    longitude: float | None
    altitude: float | None
    source: ArchiveMember
    albums: list[str] = field(default_factory=list)
    archived: bool = False
    trashed: bool = False


def is_media_name(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in MEDIA_SUFFIXES


def is_year_bucket(folder_name: str, has_album_metadata: bool) -> bool:
    """A year folder ends with a four-digit year and has no album metadata.json."""
    if has_album_metadata:
        return False
    return _YEAR_AT_END.match(folder_name) is not None


def discover_library_root(member_names: Iterable[str]) -> str:
    """Return the directory that directly contains year folders, album folders, and files.

    The product folder name (``Google Photos``, ``Google Kuvat``, ...) is not used.
    """
    children: dict[str, set[str]] = defaultdict(set)
    for name in member_names:
        if name.endswith("/"):
            continue
        parts = PurePosixPath(name).parts
        if not parts or parts[-1] == "archive_browser.html":
            continue
        for index in range(len(parts) - 1):
            parent = "/".join(parts[:index])
            children[parent].add(parts[index])
    ranked: list[tuple[int, str]] = []
    for parent, kids in children.items():
        year_kids = [kid for kid in kids if _YEAR_AT_END.match(kid)]
        if year_kids:
            ranked.append((len(year_kids), parent))
    if not ranked:
        return ""
    ranked.sort(key=lambda item: (-item[0], len(item[1])))
    return ranked[0][1]


def sidecar_name_candidates(filename: str) -> list[str]:
    """Filename patterns Google has used for the JSON next to a media file."""
    variants = [filename]
    path = PurePosixPath(filename)
    edited = _EDITED_STEM.match(path.stem)
    if edited:
        variants.append(f"{edited.group('stem')}{path.suffix}")
    numbered = _NUMBER_BEFORE_EXT.match(filename)
    number = numbered.group("number") if numbered else None
    if numbered:
        variants.append(f"{numbered.group('stem')}{numbered.group('suffix')}")

    names: list[str] = []
    for variant in variants:
        names.append(f"{variant}.json")
        names.append(f"{variant}.supplemental-metadata.json")
        if number:
            variant_path = PurePosixPath(variant)
            names.append(f"{variant_path.stem}{variant_path.suffix}({number}).json")
            names.append(f"{variant}.supplemental-metadata({number}).json")
            names.append(f"{variant_path.stem}({number}){variant_path.suffix}.json")
    return names


def _json_match_key(json_name: str) -> str:
    return _SUPPLEMENTAL_TAIL.sub("", json_name)


def match_sidecar_name(filename: str, json_names: Iterable[str]) -> str | None:
    """Pick the JSON sidecar for ``filename`` from names in the same folder."""
    available = list(json_names)
    lookup = {name: name for name in available}
    for candidate in sidecar_name_candidates(filename):
        if candidate in lookup:
            return lookup[candidate]
    folded = {name.casefold(): name for name in available}
    for candidate in sidecar_name_candidates(filename):
        found = folded.get(candidate.casefold())
        if found:
            return found

    best: tuple[int, str] | None = None
    for json_name in available:
        key = _json_match_key(json_name)
        if len(key) < 8:
            continue
        media_key = filename
        if key.casefold() == media_key.casefold()[: len(key)] or media_key.casefold().startswith(
            key.casefold()
        ):
            score = len(key)
            if best is None or score > best[0]:
                best = (score, json_name)
    if best:
        return best[1]
    return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("timestamp")
    if raw is None:
        return None
    try:
        instant = datetime.fromtimestamp(int(str(raw)), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
    return instant.replace(tzinfo=None)


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == 0.0:
        return None
    return number


def metadata_from_json(payload: dict) -> dict:
    """Normalize the fields this tool uses from a Takeout sidecar."""
    geo = payload.get("geoData") if isinstance(payload.get("geoData"), dict) else {}
    taken = _parse_time(payload.get("photoTakenTime")) or _parse_time(payload.get("creationTime"))
    return {
        "title": str(payload.get("title") or ""),
        "description": str(payload.get("description") or ""),
        "taken": taken,
        "latitude": _float_or_none(geo.get("latitude")),
        "longitude": _float_or_none(geo.get("longitude")),
        "altitude": _float_or_none(geo.get("altitude")),
        "archived": bool(payload.get("archived")),
        "trashed": bool(payload.get("trashed")),
    }


def _album_title(folder_name: str, metadata_bytes: bytes | None) -> str:
    if metadata_bytes:
        try:
            payload = json.loads(metadata_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        album_data = payload.get("albumData") if isinstance(payload, dict) else None
        if isinstance(album_data, dict) and album_data.get("title"):
            return str(album_data["title"])
        if isinstance(payload, dict) and payload.get("title"):
            return str(payload["title"])
    return folder_name


def _sha256(member: ArchiveMember) -> str:
    digest = hashlib.sha256()
    with member.open() as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _iter_members(containers: list[Path]) -> Iterator[tuple[Path, str, int]]:
    for container in containers:
        if container.is_dir():
            for path in container.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(container).as_posix()
                    yield container, relative, path.stat().st_size
            continue
        if not zipfile.is_zipfile(container):
            raise ValueError(f"Not a zip archive or directory: {container}")
        with zipfile.ZipFile(container) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                yield container, info.filename, info.file_size


class TakeoutLibrary:
    """Indexed view of one export, which may be split across several zip files."""

    def __init__(self, containers: list[Path]):
        self.containers = containers
        self.items: list[TakeoutItem] = []

    def index(self, on_hash=None) -> list[TakeoutItem]:
        listed = list(_iter_members(self.containers))
        root = discover_library_root(name for _, name, _ in listed)
        prefix = f"{root}/" if root else ""

        folders: dict[str, dict] = defaultdict(
            lambda: {
                "media": [],
                "json": {},
                "album_meta": None,
            }
        )
        for container, name, size in listed:
            if root and not (name == root or name.startswith(prefix)):
                continue
            relative = name[len(prefix) :] if prefix else name
            if not relative or relative == "archive_browser.html":
                continue
            path = PurePosixPath(relative)
            if len(path.parts) < 2:
                continue
            folder = path.parts[0]
            filename = path.name
            bucket = folders[folder]
            member = ArchiveMember(container, name, size)
            if filename == "metadata.json":
                bucket["album_meta"] = member
            elif filename.lower().endswith(".json"):
                bucket["json"][filename] = member
            elif is_media_name(filename):
                bucket["media"].append((filename, member))

        occurrences: list[MediaOccurrence] = []
        for folder, bucket in folders.items():
            has_album_meta = bucket["album_meta"] is not None
            parsed_json: dict[str, dict] = {}
            archived_votes = 0
            trashed_votes = 0
            for json_name, member in bucket["json"].items():
                try:
                    payload = json.loads(member.read_bytes().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                normalized = metadata_from_json(payload)
                parsed_json[json_name] = normalized
                archived_votes += int(normalized["archived"])
                trashed_votes += int(normalized["trashed"])
            sidecar_count = len(parsed_json)
            if is_year_bucket(folder, has_album_meta):
                role = "year"
                album_title = None
            elif sidecar_count and trashed_votes * 2 >= sidecar_count:
                role = "trash"
                album_title = None
            elif sidecar_count and archived_votes * 2 >= sidecar_count:
                role = "archive"
                album_title = None
            else:
                role = "album"
                meta_bytes = (
                    bucket["album_meta"].read_bytes() if bucket["album_meta"] is not None else None
                )
                album_title = _album_title(folder, meta_bytes)

            json_names = list(bucket["json"])
            for filename, member in bucket["media"]:
                sidecar_name = match_sidecar_name(filename, json_names)
                metadata = parsed_json.get(sidecar_name) if sidecar_name else None
                if metadata is None:
                    metadata = _metadata_by_title(filename, parsed_json)
                occurrences.append(
                    MediaOccurrence(
                        member=member,
                        folder=folder,
                        filename=filename,
                        role=role,
                        album_title=album_title,
                        metadata=metadata,
                    )
                )

        self.items = _merge_occurrences(occurrences, on_hash=on_hash)
        return self.items


def _metadata_by_title(filename: str, parsed_json: dict[str, dict]) -> dict | None:
    folded = filename.casefold()
    edited = _EDITED_STEM.match(PurePosixPath(filename).stem)
    alternatives = {folded}
    if edited:
        alternatives.add(f"{edited.group('stem')}{PurePosixPath(filename).suffix}".casefold())
    for payload in parsed_json.values():
        title = str(payload.get("title") or "").casefold()
        if title in alternatives:
            return payload
    return None


def _role_rank(role: str) -> int:
    return {"year": 0, "archive": 1, "album": 2, "trash": 3}.get(role, 9)


def _merge_occurrences(occurrences: list[MediaOccurrence], on_hash=None) -> list[TakeoutItem]:
    groups: dict[str, list[MediaOccurrence]] = defaultdict(list)
    hashes: dict[int, str] = {}
    to_hash: list[ArchiveMember] = []
    for occurrence in occurrences:
        identity = id(occurrence.member)
        if identity not in hashes:
            hashes[identity] = ""
            to_hash.append(occurrence.member)
    total = len(to_hash)
    for number, member in enumerate(to_hash, start=1):
        hashes[id(member)] = _sha256(member)
        if on_hash is not None:
            on_hash(number, total)
    for occurrence in occurrences:
        groups[hashes[id(occurrence.member)]].append(occurrence)

    items: list[TakeoutItem] = []
    for digest, group in groups.items():
        ordered = sorted(group, key=lambda item: (_role_rank(item.role), item.filename))
        keeper = ordered[0]
        if all(item.role == "trash" for item in group):
            continue
        metadata = next((item.metadata for item in ordered if item.metadata), None) or {}
        albums: list[str] = []
        for item in group:
            if item.role == "album" and item.album_title and item.album_title not in albums:
                albums.append(item.album_title)
        filename = str(metadata.get("title") or keeper.filename)
        items.append(
            TakeoutItem(
                filename=filename,
                sha256=digest,
                size=keeper.member.size,
                taken=metadata.get("taken"),
                description=str(metadata.get("description") or ""),
                latitude=metadata.get("latitude"),
                longitude=metadata.get("longitude"),
                altitude=metadata.get("altitude"),
                source=keeper.member,
                albums=albums,
                archived=any(bool((item.metadata or {}).get("archived")) for item in group),
                trashed=False,
            )
        )
    return items


# takeout-20260924T153744Z-1-001.zip -> prefix "takeout-20260924T153744Z-1-", part "001"
_PART_NAME = re.compile(r"^(?P<prefix>.*-)(?P<number>\d{3})\.zip$", re.IGNORECASE)


def expand_takeout_parts(paths: list[Path]) -> list[Path]:
    """Include every numbered sibling when one Takeout part is named.

    ``takeout-...-001.zip`` also selects ``-002.zip`` through ``-999.zip`` in
    the same directory, for the parts that exist. A directory, or a zip whose
    name does not end in ``-NNN.zip``, is used as given.
    """
    selected: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        marker = f"{path.parent}/{path.name.casefold()}"
        if marker in seen:
            return
        seen.add(marker)
        selected.append(path)

    for path in paths:
        match = _PART_NAME.match(path.name)
        if path.is_dir() or match is None:
            add(path)
            continue
        prefix = match.group("prefix").casefold()
        siblings: list[Path] = []
        if path.parent.exists():
            for entry in path.parent.iterdir():
                entry_match = _PART_NAME.match(entry.name)
                if (
                    entry.is_file()
                    and entry_match is not None
                    and entry_match.group("prefix").casefold() == prefix
                ):
                    siblings.append(entry)
        siblings.sort(key=lambda item: item.name.casefold())
        if not siblings:
            add(path)
            continue
        for sibling in siblings:
            add(sibling)
    return selected


def load_takeout(paths: list[Path], on_hash=None) -> list[TakeoutItem]:
    library = TakeoutLibrary(expand_takeout_parts(paths))
    return library.index(on_hash=on_hash)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def member_from_bytes(payload: bytes, name: str = "memory") -> ArchiveMember:
    """Test helper kept importable; production code uses real containers."""
    del payload, name
    raise NotImplementedError
