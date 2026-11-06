"""Serialized, durable config writes, with detection of external editor changes."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Concatenate, ParamSpec, TypeVar

from .fsutil import Locked, atomic_writer, exclusive_lock
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


def write_private(path: Path, text: str, *, expected: str | None = None) -> None:
    """Private durable publication through the shared safe file boundary."""
    baseline = (
        expected
        if expected is not None
        else (path.read_text(encoding="utf-8") if path.exists() else None)
    )

    def unchanged(existing: Path) -> bool:
        return baseline is not None and existing.read_text(encoding="utf-8") == baseline

    with atomic_writer(path, mode=0o600, may_replace=unchanged) as (stream, _):
        stream.write(text.encode("utf-8"))


def save_config(path: Path, original: str, text: str, *, backup: bool = True) -> None:
    """Caller holds config lock. Refuse to overwrite an external editor's newer text."""
    if path.read_text(encoding="utf-8") != original:
        raise OSError("Config changed in another editor; reload it and try again.")
    if backup:
        write_private(path.with_name(path.name + ".bak"), original)
    # Recheck after creating the backup; no fixed shared temporary filenames.
    if path.read_text(encoding="utf-8") != original:
        raise OSError("Config changed in another editor; reload it and try again.")
    write_private(path, text, expected=original)
    private_file(path)
