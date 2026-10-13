"""Filesystem helpers: Unicode normalisation, iCloud awareness, atomic writes."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

# macOS: file content lives only in iCloud; reading it triggers a download.
SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)
IGNORED_NAMES = {".DS_Store", "Icon\r"}
IGNORED_SUFFIXES = (".part", ".tmp", ".icloud")
_WINDOWS_FORBIDDEN = re.compile(r'[<>:"\\|?*\x00-\x1f]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def path_key(path: Path | str) -> str:
    """Comparison key for paths: NFC and case-folded (APFS/NTFS are case-insensitive)."""
    return nfc(str(path)).casefold()


def sanitize_filename(name: str) -> str:
    """Turn a remote file name into a safe single path component."""
    name = nfc(name).replace("/", "-")
    name = _CONTROL.sub("", name)
    if sys.platform == "win32":
        name = _WINDOWS_FORBIDDEN.sub("_", name)
    name = name.strip().rstrip(".")
    if name in ("", ".", ".."):
        name = "unnamed"
    return name


def is_ignored(name: str) -> bool:
    return name in IGNORED_NAMES or name.endswith(IGNORED_SUFFIXES) or name.startswith("._")


@dataclass(frozen=True)
class LocalStat:
    size: int
    mtime: float
    dataless: bool


def local_stat(path: Path) -> LocalStat | None:
    """stat() never triggers an iCloud download, so this is always cheap."""
    try:
        st = path.stat()
    except FileNotFoundError:
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
            if is_ignored(entry.name):
                continue
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)


def with_suffix_number(path: Path, n: int, template: str) -> Path:
    """name.pdf -> name_2.pdf (template "_{n}") or name_v2.pdf (template "_v{n}")."""
    stem, ext = split_ext(path.name)
    return path.with_name(f"{stem}{template.format(n=n)}{ext}")


def split_ext(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    if not stem:  # ".bashrc"
        return name, ""
    return stem, ext


def part_path(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


@contextmanager
def atomic_writer(dest: Path) -> Iterator[tuple[BinaryIO, Callable[[], str]]]:
    """Write to ``dest.part``; on success fsync and atomically replace ``dest``.

    Yields the open file and a function returning the sha256 of what was written.
    On any exception (including KeyboardInterrupt) the .part file is removed.
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
        os.replace(tmp, dest)
    except BaseException:
        fh.close()
        tmp.unlink(missing_ok=True)
        raise
