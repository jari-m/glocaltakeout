"""Index of files already stored under a gphotos-sync library root.

The published database is ``glocaltakeout.sqlite`` in the library root. A run
copies it to a local temp file and writes it back only when the run finishes,
because an open SQLite file on a Samba share locks and can corrupt.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from glocaltakeout.layout import PHOTOS_DIR, split_duplicate_name
from glocaltakeout.takeout import is_media_name

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    relative_path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    duplicate_no INTEGER NOT NULL,
    size INTEGER NOT NULL,
    captured TEXT,
    sha256 TEXT,
    source_sha256 TEXT
);
CREATE INDEX IF NOT EXISTS files_original ON files(original_name);
CREATE INDEX IF NOT EXISTS files_size ON files(size);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);
"""


@dataclass
class IndexedFile:
    relative_path: Path
    filename: str
    original_name: str
    duplicate_no: int
    size: int
    captured: datetime | None
    sha256: str | None
    source_sha256: str | None = None


def _format_captured(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_captured(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    return None


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class GphotosHint:
    """Read-only capture times and names from a gphotos-sync database."""

    def __init__(self, path: Path | None):
        self.by_path: dict[tuple[str, str], datetime] = {}
        self.file_count: int | None = None
        if path is None or not path.exists():
            return
        local = Path(tempfile.mkdtemp(prefix="gphotos-hint-")) / "gphotos.sqlite"
        shutil.copy2(path, local)
        uri = local.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error:
            return
        try:
            rows = connection.execute(
                "SELECT Path, FileName, OrigFileName, CreateDate FROM SyncFiles"
            )
        except sqlite3.Error:
            connection.close()
            return
        for folder, filename, _original, created in rows:
            captured = _parse_captured(None if created is None else str(created))
            if captured is None or not filename:
                continue
            folder_text = "" if folder is None else str(folder).replace("\\", "/").strip("/")
            self.by_path[(folder_text, str(filename))] = captured
        try:
            counted = connection.execute("SELECT COUNT(*) FROM SyncFiles").fetchone()
            self.file_count = int(counted[0]) if counted and counted[0] else None
        except sqlite3.Error:
            self.file_count = None
        connection.close()

    def captured(self, relative_dir: Path, filename: str) -> datetime | None:
        folder = relative_dir.as_posix()
        return self.by_path.get((folder, filename))


class DestinationIndex:
    """Working copy of the library index. Call ``publish`` to store it on the share."""

    def __init__(self, library_root: Path, hint: GphotosHint | None = None, on_scan=None):
        self.library_root = library_root
        self.share_db = library_root / "glocaltakeout.sqlite"
        self.hint = hint or GphotosHint(None)
        self._on_scan = on_scan
        self._temp = tempfile.TemporaryDirectory(prefix="glocaltakeout-index-")
        self.local_db = Path(self._temp.name) / "glocaltakeout.sqlite"
        self.rebuilt = False
        self.connection = self._open_local()

    def _open_local(self) -> sqlite3.Connection:
        if self.share_db.exists():
            shutil.copy2(self.share_db, self.local_db)
            try:
                connection = sqlite3.connect(self.local_db)
                connection.execute("SELECT source_sha256 FROM files LIMIT 1")
                return connection
            except sqlite3.DatabaseError:
                if self.local_db.exists():
                    self.local_db.unlink()
        connection = sqlite3.connect(self.local_db)
        connection.executescript(_SCHEMA)
        self.rebuilt = True
        self._scan_photos(connection)
        return connection

    def _scan_photos(self, connection: sqlite3.Connection) -> None:
        photos = self.library_root / PHOTOS_DIR
        if not photos.exists():
            return
        seen = 0
        for path in photos.rglob("*"):
            if not path.is_file() or path.is_symlink() or not is_media_name(path.name):
                continue
            seen += 1
            if self._on_scan is not None:
                self._on_scan(seen)
            relative = path.relative_to(self.library_root)
            original, number = split_duplicate_name(path.name)
            captured = self.hint.captured(relative.parent, path.name)
            connection.execute(
                """
                INSERT OR REPLACE INTO files
                    (relative_path, filename, original_name, duplicate_no, size, captured, sha256, source_sha256)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    relative.as_posix(),
                    path.name,
                    original,
                    number,
                    path.stat().st_size,
                    _format_captured(captured),
                ),
            )
        connection.commit()

    def files(self) -> list[IndexedFile]:
        rows = self.connection.execute(
            """
            SELECT relative_path, filename, original_name, duplicate_no, size, captured, sha256, source_sha256
            FROM files
            """
        )
        return [
            IndexedFile(
                relative_path=Path(relative_path),
                filename=filename,
                original_name=original_name,
                duplicate_no=duplicate_no,
                size=size,
                captured=_parse_captured(captured),
                sha256=digest,
                source_sha256=source_digest,
            )
            for relative_path, filename, original_name, duplicate_no, size, captured, digest, source_digest in rows
        ]

    def by_hash(self, digest: str) -> IndexedFile | None:
        row = self.connection.execute(
            """
            SELECT relative_path, filename, original_name, duplicate_no, size, captured, sha256, source_sha256
            FROM files WHERE sha256 = ? OR source_sha256 = ?
            """,
            (digest, digest),
        ).fetchone()
        if row is None:
            return None
        return IndexedFile(
            relative_path=Path(row[0]),
            filename=row[1],
            original_name=row[2],
            duplicate_no=row[3],
            size=row[4],
            captured=_parse_captured(row[5]),
            sha256=row[6],
            source_sha256=row[7],
        )

    def ensure_hashes_for_sizes(self, sizes: set[int], on_hash=None) -> int:
        """Hash on-disk files whose size matches a Takeout item and that are not hashed yet.

        Returns the number of files that needed a hash. ``on_hash`` receives
        ``(current, total)`` after each one.
        """
        if not sizes:
            if on_hash is not None:
                on_hash(0, 0)
            return 0
        placeholders = ",".join("?" for _ in sizes)
        rows = self.connection.execute(
            f"""
            SELECT relative_path FROM files
            WHERE sha256 IS NULL AND size IN ({placeholders})
            """,
            tuple(sizes),
        ).fetchall()
        total = len(rows)
        done = 0
        for (relative_path,) in rows:
            path = self.library_root / relative_path
            done += 1
            if path.is_file():
                digest = hash_file(path)
                self.connection.execute(
                    "UPDATE files SET sha256 = ? WHERE relative_path = ?",
                    (digest, relative_path),
                )
            if on_hash is not None:
                on_hash(done, total)
        self.connection.commit()
        return total

    def names_in_dir(self, relative_dir: Path) -> list[str]:
        prefix = relative_dir.as_posix().rstrip("/") + "/"
        rows = self.connection.execute(
            "SELECT filename, relative_path FROM files WHERE relative_path LIKE ?",
            (prefix + "%",),
        )
        wanted = relative_dir.as_posix()
        return [
            filename
            for filename, relative_path in rows
            if str(Path(relative_path).parent).replace("\\", "/") == wanted
        ]

    def remember(self, record: IndexedFile) -> None:
        self.connection.execute(
            """
            INSERT OR REPLACE INTO files
                (relative_path, filename, original_name, duplicate_no, size, captured, sha256, source_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.relative_path.as_posix(),
                record.filename,
                record.original_name,
                record.duplicate_no,
                record.size,
                _format_captured(record.captured),
                record.sha256,
                record.source_sha256,
            ),
        )
        self.connection.commit()

    def publish(self) -> None:
        """Replace the library-root database with the local working copy."""
        self.connection.commit()
        self.connection.close()
        self.library_root.mkdir(parents=True, exist_ok=True)
        temporary = self.share_db.with_name(self.share_db.name + ".writing")
        # copyfile, not copy2: a drvfs or Samba mount often rejects timestamp updates.
        shutil.copyfile(self.local_db, temporary)
        temporary.replace(self.share_db)
        self._temp.cleanup()
