"""Filesystem helpers: Unicode normalisation, iCloud awareness, atomic writes."""

from __future__ import annotations

import hashlib
import html
import os
import re
import secrets
import shutil
import stat
import sys
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# macOS: file content lives only in iCloud; reading it triggers a download.
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
IGNORED_NAMES = {".DS_Store", "Icon\r"}
IGNORED_SUFFIXES = (".part", ".tmp", ".icloud")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# Not allowed on Windows/OneDrive and not synced by iCloud for Windows; on macOS
# a ":" shows up as "/" in Finder. Replaced when names are made portable.
_NON_PORTABLE = re.compile(r'[<>"\\|?*]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.IGNORECASE)
_ENTITY = re.compile(r"&(?:[a-zA-Z]{2,8}|#\d{2,5}|#x[0-9a-fA-F]{2,4});")
# Leaves room for "_v12", "_99" and ".part" within the usual 255-byte limit.
MAX_NAME_BYTES = 240
MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/msword": ".doc",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/x-ipynb+json": ".ipynb",
}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def path_key(path: Path | str) -> str:
    """Comparison key for paths: NFC and case-folded (APFS/NTFS are case-insensitive)."""
    return nfc(str(path)).casefold()


def sanitize_filename(name: str, *, portable: bool = True, mime_type: str = "") -> str:
    """Turn a remote file name into a safe single path component.

    Always: NFC, no "/" or control characters, HTML entities decoded, no
    leading dot (hidden file), no trailing dots/spaces, at most
    MAX_NAME_BYTES of UTF-8 (the extension is kept). ``portable`` also
    replaces characters that Windows, OneDrive or iCloud for Windows reject.
    A missing extension is added from ``mime_type`` when it is well known.
    """
    if _ENTITY.search(name):
        name = html.unescape(name)
    name = nfc(name).replace("/", "-")
    name = _CONTROL.sub("", name)
    if portable or sys.platform == "win32":
        name = name.replace(": ", " - ").replace(":", "-")
        name = _NON_PORTABLE.sub("_", name)
    name = name.strip().rstrip(". ").lstrip(".").strip()
    if not name:
        name = "unnamed"
    stem, ext = split_ext(name)
    if not ext and mime_type.lower() in MIME_EXTENSIONS:
        ext = MIME_EXTENSIONS[mime_type.lower()]
    if _RESERVED.match(stem):
        stem = "_" + stem
    return truncate_name(stem, ext)


def truncate_name(stem: str, ext: str, limit: int = MAX_NAME_BYTES) -> str:
    """Shorten ``stem`` so that stem+ext fits ``limit`` UTF-8 bytes."""
    if len(ext.encode()) > 20:  # not a real extension
        stem, ext = stem + ext, ""
    budget = limit - len(ext.encode())
    encoded = stem.encode()
    if len(encoded) > budget:
        stem = encoded[:budget].decode("utf-8", errors="ignore").rstrip(" .")
    return stem + ext


def icloud_placeholder(path: Path) -> Path:
    """Older macOS keeps evicted files as ".name.icloud" placeholders."""
    return path.with_name(f".{path.name}.icloud")


def is_ignored(name: str) -> bool:
    return name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES) or name.startswith("._")


@dataclass(frozen=True)
class LocalStat:
    size: int
    mtime: float
    dataless: bool


def local_stat(path: Path) -> LocalStat | None:
    """stat() never triggers an iCloud download, so this is always cheap.

    An old-style ".name.icloud" placeholder counts as an evicted file of
    unknown size (``size == -1``).
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        try:
            ph = icloud_placeholder(path).stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return LocalStat(-1, ph.st_mtime, True)
    except NotADirectoryError:
        return None
    flags = getattr(st, "st_flags", 0)
    return LocalStat(st.st_size, st.st_mtime, bool(flags & SF_DATALESS))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def walk_files(root: Path) -> Iterator[Path]:
    """All regular files below root, skipping ignored names. Never reads content."""
    if not root.is_dir():
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") and name.endswith(".icloud") and len(name) > 8:
                yield Path(current) / name[1:-7]  # evicted file, old-style placeholder
                continue
            if is_ignored(name):
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)


def with_suffix_number(path: Path, n: int, template: str) -> Path:
    """name.pdf -> name_2.pdf (template "_{n}") or name_v2.pdf (template "_v{n}")."""
    stem, ext = split_ext(path.name)
    suffix = template.format(n=n)
    stem = truncate_name(stem, "", MAX_NAME_BYTES + 14 - len((suffix + ext).encode()))
    return path.with_name(f"{stem}{suffix}{ext}")


def split_ext(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    if not stem:  # ".bashrc"
        return name, ""
    return stem, ext


_PART = re.compile(r"\.[0-9a-f]{8}\.part$")
_UNZIP_TMP = re.compile(r"^\..+\.[a-z0-9_]{8}\.unzip$")  # tempfile.mkdtemp in extract_zip


def part_path(dest: Path) -> Path:
    """Unique temporary name next to ``dest``; parallel writers never share it."""
    base = truncate_name(dest.name, "", 200)  # stays below 255 bytes with the suffix
    return dest.with_name(f"{base}.{secrets.token_hex(4)}.part")


def cleanup_stale_parts(directory: Path, older_than: float = 24 * 3600) -> list[Path]:
    """Remove temp files this tool left behind (crash, power loss). Only our pattern."""
    removed: list[Path] = []
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return removed
    now = time.time()
    for entry in entries:
        try:
            if now - entry.stat(follow_symlinks=False).st_mtime <= older_than:
                continue
            if _PART.search(entry.name) and entry.is_file(follow_symlinks=False):
                os.unlink(entry.path)
                removed.append(Path(entry.path))
            elif _UNZIP_TMP.match(entry.name) and entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)  # an extraction interrupted by a crash
                removed.append(Path(entry.path))
        except OSError:
            pass
    return removed


@contextmanager
def atomic_writer(
    dest: Path, commit: Callable[[str], bool] | None = None
) -> Iterator[tuple[BinaryIO, Callable[[], str]]]:
    """Write to a temp file; on success fsync and atomically replace ``dest``.

    Yields the open file and a function returning the sha256 of what was written.
    ``commit(sha)`` returning False discards the result instead of replacing
    ``dest``. On any exception (including KeyboardInterrupt) the temp file is
    removed.
    """
    tmp = part_path(dest)
    digest = hashlib.sha256()

    class _Hashing:
        def __init__(self, fh: BinaryIO) -> None:
            self._fh = fh

        def write(self, data: bytes) -> int:
            digest.update(data)
            return self._fh.write(data)

    fh = tmp.open("wb")
    try:
        yield _Hashing(fh), digest.hexdigest  # type: ignore[misc]
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        if commit is not None and not commit(digest.hexdigest()):
            tmp.unlink(missing_ok=True)
            return
        os.replace(tmp, dest)
        fsync_dir(dest.parent)
    except BaseException:
        fh.close()
        tmp.unlink(missing_ok=True)
        raise


def fsync_dir(directory: Path) -> None:
    """Persist a rename in ``directory`` (POSIX); best effort elsewhere."""
    if sys.platform == "win32":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some file systems (e.g. network shares) do not support it
    finally:
        os.close(fd)


def move_no_clobber(src: Path, dst: Path) -> None:
    """Rename without ever replacing an existing file (link+unlink is atomic on POSIX)."""
    if dst.exists():
        raise FileExistsError(f"{dst} exists")
    try:
        os.link(src, dst)  # fails if dst exists
    except OSError:
        # cross-device or filesystem without hard links: check-then-rename
        if dst.exists():
            raise
        os.rename(src, dst)
        return
    os.unlink(src)


class Locked(Exception):
    pass


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Process-wide lock so two syncs (e.g. scheduler + manual) never run at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    if sys.platform != "win32":
        with suppress(OSError):
            os.chmod(path, 0o600)
    try:
        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise Locked("another studip-router run is in progress") from exc
        else:
            import fcntl

            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Locked("another studip-router run is in progress") from exc
        yield
    finally:
        fh.close()  # releases the lock
