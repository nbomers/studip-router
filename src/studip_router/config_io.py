"""Serialized, durable config writes, with detection of external editor changes."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Concatenate, ParamSpec, TypeVar

from .fsutil import Locked, exclusive_lock, fsync_dir
from .paths import private_file

P = ParamSpec("P")
T = TypeVar("T")


def config_locked(fn: Callable[Concatenate[Path, P], T]) -> Callable[Concatenate[Path, P], T]:
    @wraps(fn)
    def locked(path: Path, /, *args: P.args, **kwargs: P.kwargs) -> T:
        try:
            with exclusive_lock(path.with_name(path.name + ".lock")):
                return fn(path, *args, **kwargs)
        except Locked as exc:
            raise OSError("Config is being changed by another process; try again.") from exc

    return locked


def write_private(path: Path, text: str) -> None:
    """Unique private temporary file; flush data before replacing the destination."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name[:40]}.", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def save_config(path: Path, original: str, text: str, *, backup: bool = True) -> None:
    """Caller holds config lock. Refuse to overwrite an external editor's newer text."""
    if path.read_text(encoding="utf-8") != original:
        raise OSError("Config changed in another editor; reload it and try again.")
    if backup:
        write_private(path.with_name(path.name + ".bak"), original)
    # Recheck after creating the backup; no fixed shared temporary filenames.
    if path.read_text(encoding="utf-8") != original:
        raise OSError("Config changed in another editor; reload it and try again.")
    write_private(path, text)
    private_file(path)
