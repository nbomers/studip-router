"""Check after an update: does the new version sort your synced files differently?

A new version can change the keyword lists or the automatic sorting. The
background sync shows no preview, so after an update it waits until you have
looked (the UI, or ``studip sync`` by hand, including ``--dry-run``), but only
when files you already have would now go to another folder, and for at most
``HOLD_DAYS`` days, so a forgotten notice never stops the sync for good.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass

from . import __version__, paths
from .config import Config
from .reorganize import pending_moves
from .state import State

HOLD_DAYS = 3


@dataclass(frozen=True)
class UpdateNotice:
    previous: str  # the version seen before, or "an older version"
    moves: int  # synced files the new version would put elsewhere


def seen_version() -> str | None:
    try:
        return paths.seen_version_file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def mark_seen() -> None:
    """You have seen this version's sorting (UI or a sync by hand): auto-sync may run."""
    path = paths.seen_version_file()
    with contextlib.suppress(OSError):
        paths.private_dir(path.parent)
        path.write_text(__version__ + "\n", encoding="utf-8")
    paths.update_hold_file().unlink(missing_ok=True)


def check(cfg: Config, state: State) -> UpdateNotice | None:
    """A notice when this version is new and sorts synced files differently."""
    seen = seen_version()
    if seen == __version__:
        return None
    if seen is None and not state.all():  # a fresh install: nothing to compare
        mark_seen()
        return None
    moves = len(pending_moves(cfg, state))
    if not moves:
        mark_seen()
        return None
    return UpdateNotice(seen or "an older version", moves)


def hold_background(cfg: Config, state: State) -> tuple[UpdateNotice, bool] | None:
    """For the background sync: (notice, first time) while it should wait, else None."""
    notice = check(cfg, state)
    if notice is None:
        return None
    hold = paths.update_hold_file()
    try:
        since = float(hold.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            paths.private_dir(hold.parent)
            hold.write_text(f"{time.time()}\n", encoding="utf-8")
        return notice, True
    if time.time() - since > HOLD_DAYS * 86400:
        mark_seen()  # waited long enough: sync again, the UI still lists the moves
        return None
    return notice, False


def message(notice: UpdateNotice) -> str:
    return (
        f"studip-router {__version__} would put {notice.moves} of your synced files into"
        f" other folders (before: {notice.previous}). Open studip to check; auto-sync waits"
        f" up to {HOLD_DAYS} days."
    )
