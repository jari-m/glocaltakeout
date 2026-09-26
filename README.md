# glocaltakeout

`glocaltakeout` is a utility for processing Google Photos archives created by [Google Takeout](https://takeout.google.com). The rationale is to enable you to unpack your Google Photos Takeout archive content to a local USB drive or similar, so that it is safe for you to delete the photos from Google and free up storage space. When you then repeat the Takeout process some months later, `glocaltakeout` knows which files you have already downloaded and will ignore those. It is also compatible with the discontinued [gphotos-sync](https://github.com/gilesknap/gphotos-sync) in that it can read its sync database. It copies photos and videos from a Google Takeout export into a library folder. The folder can be an existing `gphotos-sync` tree, or an empty directory. An empty directory has no earlier files to compare, so every Takeout file is copied and `photos/` and `albums/` folders with yearly and monthly subfolders are created in the gphotos-sync layout. The `gphotos-sync` database `gphotos.sqlite` is not required, but if it exists it will be used to speed up the resolving of which files are already present from previous backups.

## Installation

Python 3.8 or newer is required, including the 3.8.20 build available on Raspbian Buster. This project is developed on 3.10. macOS does not include Python; install it from Homebrew or python.org.

From a checkout of this repository:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

On Windows, activate with `.venv\Scripts\activate` instead of `source`. The `[dev]` extra installs pytest. Use `pip install -e .` when you do not need the tests. Dependencies are declared in `pyproject.toml`.

## Options

```text
glocaltakeout SOURCE [SOURCE ...] --library DIR [--apply] [--report FILE]
    [--gphotos-db FILE] [--case-insensitive | --no-case-insensitive]
    [--normalize-filenames nfc]
glocaltakeout --link-only --library DIR [--report FILE]
```
Example: 
```bash
glocaltakeout takeout-20260926T113224Z-1-001.zip --library /media/USBHDD/my-google-photos \
  --apply --case-insensitive
```

`SOURCE` is a Takeout zip or an extracted Takeout directory. Pass the first zip only. A name ending in `-001.zip` also includes `-002.zip` through `-999.zip` in that same directory, for every part that exists.

`--library` is the folder that should contain `photos/` and `albums/`. It is created if needed. It can be empty or an existing gphotos-sync tree.

`--apply` copies new files and updates album links. Without it, the run only writes the report and refreshes `glocaltakeout.sqlite`. Existing photos are not changed.

`--report` sets the JSON report path. The default is `glocaltakeout-report.json` in the current directory. The report records the library folder, the Takeout sources, and the options used for that run.

`--link-only` creates the album symlinks and does not read the Takeout archives or copy photos. This can be useful if you ran glocaltakeout over a SAMBA mount and file links were not created. You can the update just the links by running this on the file hosting device, such as a Raspberry Pi. Without `--report` it reads `albums-pending.json` in the library. With `--report` it reads that report instead, and does not modify the report. Targets in `albums-pending.json` are relative to the library. An older pending file whose targets are absolute paths is still accepted. An older report whose `pending_albums` list is only link paths is still accepted, using each decision's `path` as the photo.

`--gphotos-db` points at `gphotos.sqlite`. The default is that file in the library root, and it is read only. The run continues if the file is missing.

`--case-insensitive` treats `IMG.jpg` and `img.jpg` as the same name. This is the default on Windows. `--no-case-insensitive` forces a case-sensitive comparison. On macOS, pass `--case-insensitive` for an internal or exFAT disk, and leave it off for a case-sensitive network share.

`--normalize-filenames nfc` compares and writes filenames in composed Unicode. It is off unless you set it. Pass it on macOS.

## What it does

gphotos-sync downloaded files through the Google Photos Library API. Google has discontinued that API, and those downloads were often re-encoded, with GPS removed. A Takeout export is a separate copy of the same shots, usually at the original quality, so the bytes may not match the files already on disk.

For each unique file in the Takeout:

1. If those exact bytes are already under `photos/`, nothing is copied. Album links use that existing path.
2. If the same capture is already there (original filename, ignoring a  `(n)` suffix, and taken time within two minutes) but the bytes differ, the old file stays and the Takeout file is written beside it as the next `name (n).ext` in that same folder.
3. Otherwise the file is written to `photos/YYYY/MM/` from the taken time. A filename clash with a different capture also uses the next  `(n)` suffix. The report separates these two cases.

Takeout repeats each file in a year folder and in every album. The tool keeps one copy of each byte sequence and prefers the year-folder file when it has to choose which zip member to read.

New copies get their taken time as the file modification time when the drive allows it. JPEG files also get the taken time, GPS, and description written into EXIF. Video, HEIC, and other non-JPEG files get a JSON sidecar instead. If the drive rejects the timestamp, the run warns once and keeps the time in that EXIF or sidecar. Files that were already in the library are not modified.

## Folder layout

This matches the [gphotos-sync folder layout](https://github.com/gilesknap/gphotos-sync/blob/main/docs/explanations/folders.rst):

- Real files live in `photos/YYYY/MM/`.
- A second file with the same original name in that folder is `name (n).ext`, with a space before the parenthesis.
- Albums live in `albums/YYYY/MM Album title/` and point at the real files with relative symlinks.

An existing album folder whose name ends with the Takeout album title is reused. Old symlinks are left in place, so an album can show both the earlier API copy and the Takeout original. If the drive cannot create a symlink, the photos are still copied and the missing links are listed in `albums-pending.json` at the library root. `--link-only` creates those links later, without reading the Takeout archives again.

## Takeout folder roles

Folder names are not translated. The tool reads the archive shape:

- The library root is the directory under `Takeout/` that holds the media. `Google Photos`, and other translated  product-folder names such as `Google Kuvat` (Finnish) are ignored. `archive_browser.html` is ignored.
- A year bucket is a folder whose name ends in a four-digit year (`19xx` or `20xx`) and that has no album `metadata.json`.
- Archive and trash come from sidecar JSON: `"archived": true` and `"trashed": true`. Archive files are copied into `photos/YYYY/MM/` but do not become an album. Trash files are not copied.
- Every other folder is a user album. `metadata.json` supplies the title when the folder name was truncated.

Sidecar names can be `name.jpg.json`, `name.jpg.supplemental-metadata.json`, a truncated supplemental suffix, a  `(n)` or `(n)` duplicate marker, or the JSON of the original file for an `-edited` copy. Media and JSON may sit in different zip parts. All parts are indexed before anything is copied.

## Index on the library drive

The files under `photos/` and `albums/` are the source of truth. The tool also keeps `glocaltakeout.sqlite` in the library root, next to `photos/`, so a replaced PC does not have to walk the whole library again. Rebuilding that index means stating every file and hashing the ones whose size matches a Takeout item.

SQLite locking on a Samba share can corrupt a database that stays open there. A run copies `glocaltakeout.sqlite` to a local temp file, works on the copy, and writes it back only when the run finishes. If the share copy is missing or unreadable, it is rebuilt from the files.

`gphotos.sqlite` is optional and read-only. Capture times come from that file first. `gphotos.sqlite.previous` fills in files the latest database no longer lists, and a JPEG's own date is read only when both files miss it. A time already stored in `glocaltakeout.sqlite` is left as it is. The gphotos database is not proof the file is still on disk, and its file sizes will not match Takeout originals. This tool does not write into `gphotos.sqlite`.

## Symlinks, Windows, and network drives

Album entries are relative symlinks. That works on Linux and on WSL. On Windows 10 and 11, creating a symlink needs Developer Mode or an elevated process. On a Samba share, the client mount needs `mfsymlinks` (or the server must store real symlinks). The target path must already be visible to the operating system: a drive letter, a UNC path, a WSL `/mnt/...` path, or a mounted share. The tool does not speak SMB itself.

Windows can open `\\fileserver\drivename` directly. WSL does not see that path until you mount it. This uses the Windows connection you already have, so there is no second Samba login:

```bash
sudo mkdir -p /mnt/drivename
sudo mount -t drvfs '\\fileserver\drivename' /mnt/drivename
ls /mnt/drivename
```

`ls` should show the library root, including `photos/`. A dry run then uses `--library /mnt/drivename`. When you are finished:

```bash
sudo umount /mnt/drivename
```

This mount is for reading and copying files. It does not create album symlinks. After the photos are copied, run `--link-only` on a machine that can create them, such as the Raspberry Pi with the disk attached:

```bash
glocaltakeout --link-only --library /path/to/library --report /path/to/glocaltakeout-report.json
```

If `drvfs` cannot find `fileserver`, map the share to a drive letter in Windows first (`net use Z: \\fileserver\drivename`) and mount that letter instead:

```bash
sudo mkdir -p /mnt/drivename
sudo mount -t drvfs 'Z:' /mnt/drivename
```

## macOS

The defaults stay as they are for Windows, WSL, and Linux. On a Mac, pass the options that match the disk you are writing to.

Install Python 3.8 or newer from Homebrew or python.org. macOS does not provide it. In Terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

A Finder-mounted share is a normal path under `/Volumes/`, for example `--library /Volumes/photos`.

`--case-insensitive` compares `IMG.jpg` and `img.jpg` as the same name. Pass it when the library is on the Mac's internal disk or on an exFAT drive. Those volumes are case-insensitive. Do not pass it for the Raspberry share: that filesystem is case-sensitive, and the flag would treat two different names as one file.

`--normalize-filenames nfc` compares and writes filenames in composed Unicode (NFC). Pass it on macOS. A Mac stores names such as `café.jpg` in a decomposed form, and Takeout uses the composed form. With this flag the two match, and a new file is written in the composed form so Linux still sees the usual name. Leave it off on Windows, WSL, and Linux.

Album symlinks work on a local Mac disk. Finder's SMB client does not create Unix symlinks on a Samba share, so a run from a Mac against the Raspberry share copies the photos and lists the album links in `albums-pending.json`. Create those links later with `--link-only` on the Pi, or on another Linux machine that can write symlinks to that disk.

```bash
glocaltakeout ~/Downloads/takeout-20260924T153744Z-1-001.zip \
  --library /Volumes/photos \
  --case-insensitive \
  --normalize-filenames nfc
```

Drop `--case-insensitive` when `--library` is the Raspberry share rather than a Mac disk.

## Usage

Install in a virtual environment, then point the command at the first Takeout zip and the library folder:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

glocaltakeout /path/to/takeout-20260924T153744Z-1-001.zip --library /path/to/library
glocaltakeout /path/to/takeout-20260924T153744Z-1-001.zip --library /path/to/library --apply
```

Pass the first zip only. A name ending in `-001.zip` also includes `-002.zip` through `-999.zip` in that same directory, for every part that is actually there. You do not list each piece. A folder of already extracted files can be passed instead of a zip. `--library` is the folder that should contain `photos/` and `albums/`; it is created if needed, and it does not have to contain a previous gphotos-sync download.

The command without `--apply` refreshes the index and writes a report. It does not copy media or change `albums/`. `--apply` copies. A report-only run is not required before `--apply`. `--report` sets the report path (default: `glocaltakeout-report.json` in the current directory). `--gphotos-db` points at `gphotos.sqlite` when it is not in the library root. `--case-insensitive` compares destination filenames without case; this is the default on Windows. `--normalize-filenames nfc` compares and writes filenames in composed Unicode; it is off unless you set it. See the macOS section for when to pass each one.

When the photos are already copied and only the album symlinks are missing, create them on a machine that can write symlinks to the disk:

```bash
glocaltakeout --link-only --library /path/to/library --report /path/to/glocaltakeout-report.json
```

`--link-only` does not read the Takeout archives. Without `--report` it uses `albums-pending.json` in the library. The report file is left unchanged.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The tests use small synthetic zip files. They do not need a network connection or a real photo library.

## Attribution

The layout this tool continues is the one documented by gphotos-sync, which is licensed under Apache-2.0. This project does not copy that source code and does not depend on the `gphotos-sync` package. If a later change copies a file from that project, that file stays under Apache-2.0 with its original header, and a `NOTICE` file will say what was reused.