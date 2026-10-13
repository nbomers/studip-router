"""Apply a plan. Never deletes; every write goes through ``atomic_writer``."""

from __future__ import annotations

import logging
import os
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .api import ApiError
from .fsutil import atomic_writer, local_stat, nfc, sha256_file
from .models import RemoteFile
from .planner import Action, CoursePlan, Kind
from .postprocess import PostprocessError, extract_zip
from .state import FileRecord, State

log = logging.getLogger(__name__)

Downloader = Callable[[str, Callable[[bytes], None]], None]


@dataclass
class Outcome:
    done: list[Action] = field(default_factory=list)
    failed: list[tuple[Action, str]] = field(default_factory=list)
    extracted: list[Path] = field(default_factory=list)


def execute(
    plan: CoursePlan,
    state: State,
    download: Downloader,
    run_id: int,
    on_progress: Callable[[Action], None] | None = None,
) -> Outcome:
    outcome = Outcome()
    for action in plan.actions:
        if action.kind in (Kind.SKIP, Kind.ERROR):
            continue
        try:
            _apply(action, state, download, run_id)
        except (ApiError, OSError) as exc:
            log.error("failed: %s -> %s: %s", _label(action), action.dest, exc)
            outcome.failed.append((action, str(exc)))
            continue
        outcome.done.append(action)
        if on_progress:
            on_progress(action)
        if action.unzips:
            _unzip(action, state, run_id, outcome)
    return outcome


def _unzip(action: Action, state: State, run_id: int, outcome: Outcome) -> None:
    assert action.dest is not None
    st = local_stat(action.dest)
    if st is None or st.dataless:
        return  # never pull an archive out of iCloud just to extract it
    try:
        folder = extract_zip(action.dest)
    except (PostprocessError, OSError, zipfile.BadZipFile) as exc:
        log.error("unzip failed for %s: %s", action.dest, exc)
        outcome.failed.append((action, f"unzip failed: {exc}"))
        return
    if folder is not None:
        state.journal(run_id, "unzip", str(folder), src=str(action.dest))
        outcome.extracted.append(folder)
        log.info("unzip %s -> %s", action.dest, folder)


def _apply(action: Action, state: State, download: Downloader, run_id: int) -> None:
    assert action.dest is not None
    if action.kind is Kind.MKDIR:
        action.dest.mkdir(parents=True, exist_ok=True)
        state.journal(run_id, "mkdir", str(action.dest))
        log.info("mkdir %s", action.dest)
        return

    file = action.file
    assert file is not None
    if action.kind is Kind.ADOPT:
        st = local_stat(action.dest)
        assert st is not None
        digest = None if st.dataless else sha256_file(action.dest)
        state.upsert(_record(action, file, action.dest, st.size, st.mtime, digest, 1))
        log.info("adopt %s", action.dest)
        return

    # DOWNLOAD / NEW_VERSION / OVERWRITE
    if action.kind is not Kind.OVERWRITE and action.dest.exists():
        # Plan is stale (file appeared meanwhile): never clobber.
        raise OSError(f"refusing to overwrite unexpected file {action.dest}")
    with atomic_writer(action.dest) as (fh, digest):
        download(file.id, fh.write)
    sha = digest()
    mtime = file.chdate.timestamp()
    os.utime(action.dest, (mtime, mtime))
    size = action.dest.stat().st_size
    if size != file.size:
        log.warning("size mismatch for %s: expected %s, got %s", action.dest, file.size, size)
    state.upsert(_record(action, file, action.dest, size, mtime, sha, action.version))
    state.journal(run_id, action.kind.value, str(action.dest))
    log.info("%s %s", action.kind.value, action.dest)


def _record(
    action: Action,
    file: RemoteFile,
    path: Path,
    size: int,
    mtime: float,
    sha: str | None,
    version: int,
) -> FileRecord:
    route = action.route
    previous = action.previous
    return FileRecord(
        file_id=file.id,
        course_id=file.course_id,
        remote_name=file.name,
        remote_folder_path=file.folder_path,
        chdate=file.chdate.isoformat(),
        filesize=file.size,
        local_path=nfc(str(path)),
        local_size=size,
        local_mtime=mtime,
        sha256=sha,
        version=version,
        rule_id=route.rule_id if route else (previous.rule_id if previous else None),
        routed_by_default=(
            route.by_default if route else (previous.routed_by_default if previous else False)
        ),
    )


def _label(action: Action) -> str:
    return action.file.name if action.file else str(action.dest)
