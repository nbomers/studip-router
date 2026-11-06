"""Filesystem helpers: Unicode normalisation, iCloud awareness, atomic writes."""

from __future__ import annotations

import ctypes
import errno
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
# Control characters, and bidi controls that make "Lösung\u202efdp.exe" show as
# "Lösungexe.pdf" (U+200E/F, U+202A-E, U+2066-9).
_CONTROL = re.compile(r"[\x00-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
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


class ReplaceRefused(FileExistsError):
    """``may_replace`` said no: the existing file changed and is kept."""


def publish(tmp: Path, dest: Path, may_replace: Callable[[Path], bool] | None = None) -> None:
    """Move a finished temp file to ``dest``: the one way files reach the user's folders.

    Without ``may_replace`` an existing ``dest`` is never replaced (FileExistsError),
    also one that appears at the last moment. With it, an existing ``dest`` is replaced
    only if ``may_replace(dest)`` - called right before the replace - returns True
    (else ReplaceRefused); it may also raise to explain why.
    """
    if may_replace is None or not os.path.lexists(dest):
        move_no_clobber(tmp, dest)
        return
    if not may_replace(dest):
        raise ReplaceRefused(f"{dest.name} changed meanwhile; kept, not replaced")
    os.replace(tmp, dest)
    fsync_dir(dest.parent)


@contextmanager
def atomic_writer(
    dest: Path,
    commit: Callable[[str], bool] | None = None,
    *,
    may_replace: Callable[[Path], bool] | None = None,
    mode: int = 0o666,
) -> Iterator[tuple[BinaryIO, Callable[[], str]]]:
    """Write to a temp file; on success fsync and move it to ``dest`` with ``publish``.

    Yields the open file and a function returning the sha256 of what was written.
    ``commit(sha)`` returning False discards the result. On any exception (including
    KeyboardInterrupt) the temp file is removed.

    Safe by default: an existing ``dest`` is never replaced (FileExistsError), also
    one the user saved during a long download. To replace a file on purpose, pass
    ``may_replace``, which checks it right before the replace (see ``publish``).
    """
    tmp = part_path(dest)
    digest = hashlib.sha256()

    class _Hashing:
        def __init__(self, fh: BinaryIO) -> None:
            self._fh = fh

        def write(self, data: bytes) -> int:
            digest.update(data)
            return self._fh.write(data)

    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    fh = os.fdopen(fd, "wb")
    try:
        yield _Hashing(fh), digest.hexdigest  # type: ignore[misc]
        fh.flush()
        os.fsync(fh.fileno())
        fh.close()
        if commit is not None and not commit(digest.hexdigest()):
            tmp.unlink(missing_ok=True)
            return
        publish(tmp, dest, may_replace)
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
    except FileExistsError:
        raise
    except OSError:
        # Some filesystems cannot hard-link. Never fall back to replacing rename.
        rename_no_replace(src, dst)
    else:
        os.unlink(src)
    fsync_dir(dst.parent)  # the rename survives a power loss
    if src.parent != dst.parent:
        fsync_dir(src.parent)


def rename_no_replace(src: Path, dst: Path) -> None:
    """Atomically rename without replacing files, symlinks or directories.

    Fail closed if the platform/filesystem lacks an exclusive rename operation.
    A cross-device move also fails, leaving the source untouched.
    """
    if sys.platform == "win32":
        os.rename(src, dst)  # Windows rename fails when dst exists
        return
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        operation = libc.renameat2
        operation.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(src), -100, os.fsencode(dst), 1)  # RENAME_NOREPLACE
    elif sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        operation = libc.renamex_np
        operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(os.fsencode(src), os.fsencode(dst), 4)  # RENAME_EXCL
    else:
        raise OSError(errno.ENOTSUP, "atomic rename without replacing is unavailable", str(dst))
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(dst))


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
