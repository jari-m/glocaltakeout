# glocaltakeout

`glocaltakeout` copies photos and videos from a Google Takeout export into a library folder that [gphotos-sync](https://github.com/gilesknap/gphotos-sync) already created. It adds files that are not already there and leaves the older copies in place.

Requires Python 3.10 or newer. Developed and tested on 3.10.

## What it does

gphotos-sync downloaded files through the Google Photos Library API. Google has discontinued that API, and those downloads were often re-encoded, with GPS removed. A Takeout export is a separate copy of the same shots, usually at the original quality, so the bytes do not match the files already on disk.

For each unique file in the Takeout:

1. If those exact bytes are already under `photos/`, nothing is copied. Album links use that existing path.
2. If the same capture is already there (original filename, ignoring a ` (n)` suffix, and taken time within two minutes) but the bytes differ, the old file stays and the Takeout file is written beside it as the next `name (n).ext` in that same folder.
3. Otherwise the file is written to `photos/YYYY/MM/` from the taken time. A filename clash with a different capture also uses the next ` (n)` suffix. The report separates these two cases.

Takeout repeats each file in a year folder and in every album. The tool keeps one copy of each byte sequence and prefers the year-folder file when it has to choose which zip member to read.

New copies get their taken time as the file modification time. JPEG files also get the taken time, GPS, and description written into EXIF. Video, HEIC, and other non-JPEG files get a JSON sidecar instead. Files that were already in the library are not modified.

## Folder layout

This matches the [gphotos-sync folder layout](https://gilesknap-org.github.io/gphotos-sync/main/explanations/folders.html):

- Real files live in `photos/YYYY/MM/`.
- A second file with the same original name in that folder is `name (n).ext`, with a space before the parenthesis.
- Albums live in `albums/YYYY/MM Album title/` and point at the real files with relative symlinks.

An existing album folder whose name ends with the Takeout album title is reused. Old symlinks are left in place, so an album can show both the earlier API copy and the Takeout original. If the drive cannot create a symlink, the photos are still copied and the missing links are listed in `albums-pending.json` at the library root.

## Takeout folder roles

Folder names are not translated. The tool reads the archive shape:

- The library root is the directory under `Takeout/` that holds the media. `Google Photos`, `Google Kuvat`, and other product-folder names are ignored. `archive_browser.html` is ignored.
- A year bucket is a folder whose name ends in a four-digit year (`19xx` or `20xx`) and that has no album `metadata.json`. `Vuosi 2026` and `Photos from 2026` both match. A name such as `asuntomessut 2026 ja remontti-ideat` does not, because the year is not at the end.
- Archive and trash come from sidecar JSON: `"archived": true` and `"trashed": true`. Archive files are copied into `photos/YYYY/MM/` but do not become an album. Trash files are not copied.
- Every other folder is a user album. `metadata.json` supplies the title when the folder name was truncated.

Sidecar names can be `name.jpg.json`, `name.jpg.supplemental-metadata.json`, a truncated supplemental suffix, a ` (n)` or `(n)` duplicate marker, or the JSON of the original file for an `-edited` copy. Media and JSON may sit in different zip parts. All parts are indexed before anything is copied.

## Index on the library drive

The files under `photos/` and `albums/` are the source of truth. The tool also keeps `glocaltakeout.sqlite` in the library root, next to `photos/`, so a replaced PC does not have to walk the whole library again. Rebuilding that index means stating every file and hashing the ones whose size matches a Takeout item.

SQLite locking on a Samba share can corrupt a database that stays open there. A run copies `glocaltakeout.sqlite` to a local temp file, works on the copy, and writes it back only when the run finishes. If the share copy is missing or unreadable, it is rebuilt from the files.

`gphotos.sqlite` is optional and read-only. It can supply the capture time and the name gphotos-sync used, which avoids reading EXIF from every old file. It is not proof the file is still on disk, and its file sizes will not match Takeout originals. This tool does not write into `gphotos.sqlite`.

## Symlinks, Windows, and network drives

Album entries are relative symlinks. That works on Linux and on WSL. On Windows 10 and 11, creating a symlink needs Developer Mode or an elevated process. On a Samba share, the client mount needs `mfsymlinks` (or the server must store real symlinks). The target path must already be visible to the operating system: a drive letter, a UNC path, a WSL `/mnt/...` path, or a mounted share. The tool does not speak SMB itself.

## Usage

Install in a virtual environment, then run a dry run before copying:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

glocaltakeout /path/to/takeout-001.zip /path/to/takeout-002.zip --library /path/to/gphotos-root
glocaltakeout /path/to/takeout-001.zip --library /path/to/gphotos-root --apply
```

Without `--apply` the run refreshes the index and writes a report, and it does not copy media or change `albums/`. `--report` sets the report path (default: `glocaltakeout-report.json` in the current directory). `--gphotos-db` points at `gphotos.sqlite` when it is not in the library root. `--case-insensitive` compares destination filenames without case; this is the default on Windows.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests use small synthetic zip files. They do not need a network connection or a real photo library.

## Attribution

The layout this tool continues is the one documented by gphotos-sync, which is licensed under Apache-2.0. This project does not copy that source code and does not depend on the `gphotos-sync` package. If a later change copies a file from that project, that file stays under Apache-2.0 with its original header, and a `NOTICE` file will say what was reused.
