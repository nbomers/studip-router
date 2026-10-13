"""Apply a plan. Never deletes; every write goes through ``atomic_writer``."""

from __future__ import annotations

import logging
import os
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .api import ApiError
from .fsutil import atomic_writer, local_stat, move_no_clobber, nfc, sha256_file
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
            if action.file is not None:
                state.record_failure(action.file.id, str(exc))
            continue
        if action.file is not None and action.transfers:
            state.clear_failure(action.file.id)
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
    previous = action.previous
    if action.kind is Kind.ADOPT:
        st = local_stat(action.dest)
        assert st is not None
        digest = None if st.dataless else sha256_file(action.dest)
        _save(state, action, file, action.dest, st.size, st.mtime, digest, action.version)
        log.info("adopt %s", action.dest)
        return

    if action.kind is Kind.REFRESH:  # only Stud.IP details changed
        assert previous is not None
        _save(
            state,
            action,
            file,
            Path(previous.local_path),
            previous.local_size,
            previous.local_mtime,
            previous.sha256,
            previous.version,
        )
        return

    if action.kind is Kind.MOVE:  # renamed on Stud.IP: follow with the local copy
        assert previous is not None
        move_no_clobber(Path(previous.local_path), action.dest)
        state.journal(run_id, "move", str(action.dest), src=previous.local_path)
        _save(
            state,
            action,
            file,
            action.dest,
            previous.local_size,
            previous.local_mtime,
            previous.sha256,
            previous.version,
        )
        log.info("rename %s -> %s", previous.local_path, action.dest)
        return

    # DOWNLOAD / NEW_VERSION / OVERWRITE
    if action.kind is not Kind.OVERWRITE and action.dest.exists():
        # Plan is stale (file appeared meanwhile): never clobber.
        raise OSError(f"refusing to overwrite unexpected file {action.dest}")
    # A changed date often means a renamed or re-uploaded but identical file:
    # compare the content before creating a "_v2" copy of the same bytes.
    known = (
        previous.sha256
        if previous is not None
        and action.kind is not Kind.DOWNLOAD
        and previous.filesize == file.size
        and previous.sha256
        else None
    )
    sha, written = fetch_verified(file, action.dest, download, unless_sha=known)
    if not written:
        assert previous is not None
        log.info("%s: content unchanged on Stud.IP, nothing written", file.name)
        action.kind = Kind.REFRESH
        action.note = "same content as before, nothing written"
        _save(
            state,
            action,
            file,
            Path(previous.local_path),
            previous.local_size,
            previous.local_mtime,
            previous.sha256,
            previous.version,
        )
        return
    mtime = file.chdate.timestamp()
    os.utime(action.dest, (mtime, mtime))
    size = action.dest.stat().st_size
    _save(state, action, file, action.dest, size, mtime, sha, action.version)
    state.journal(run_id, action.kind.value, str(action.dest))
    log.info("%s %s", action.kind.value, action.dest)


def _save(
    state: State,
    action: Action,
    file: RemoteFile,
    path: Path,
    size: int,
    mtime: float,
    sha: str | None,
    version: int,
) -> None:
    state.upsert(_record(action, file, path, size, mtime, sha, version))
    previous = action.previous
    if previous is not None and previous.file_id != file.id:  # re-upload replaced it
        state.delete_file_record(previous.file_id)


_sleep = time.sleep  # replaced in tests


class DownloadError(OSError):
    """The received content is not the file we expected."""


class _Guard:
    """Counts bytes and rejects HTML error/login pages served instead of a file."""

    def __init__(self, file: RemoteFile, sink: Callable[[bytes], object]) -> None:
        self.file = file
        self.sink = sink
        self.size = 0

    def write(self, chunk: bytes) -> None:
        if self.size == 0 and chunk and not _expects_html(self.file):
            head = chunk[:512].lstrip().lower()
            if head.startswith((b"<!doctype html", b"<html")):
                raise DownloadError(
                    "received an HTML page instead of the file "
                    "(expired login, missing permission or a link to a website?)"
                )
        self.size += len(chunk)
        self.sink(chunk)


def _expects_html(file: RemoteFile) -> bool:
    return file.name.lower().endswith((".html", ".htm")) or "html" in file.mime_type.lower()


def fetch_verified(
    file: RemoteFile,
    dest: Path,
    download: Downloader,
    attempts: int = 3,
    unless_sha: str | None = None,
) -> tuple[str, bool]:
    """Download into ``dest`` atomically; return (sha256, written).

    Interrupted transfers and size mismatches are retried. If the size reported
    by Stud.IP is wrong but two complete downloads agree, the file is accepted.
    With ``unless_sha``, identical content is discarded instead of written.
    """
    mismatch: int | None = None
    for attempt in range(1, attempts + 1):
        try:
            commit = (lambda sha: sha != unless_sha) if unless_sha else None
            with atomic_writer(dest, commit) as (fh, digest):
                guard = _Guard(file, fh.write)
                download(file.id, guard.write)
                if file.size > 0 and guard.size != file.size and guard.size != mismatch:
                    mismatch = guard.size
                    raise DownloadError(
                        f"incomplete download: got {guard.size} of {file.size} bytes"
                    )
            if mismatch is not None:
                log.warning(
                    "%s: Stud.IP reports %s bytes, file has %s", file.name, file.size, mismatch
                )
            sha = digest()
            return sha, sha != unless_sha
        except DownloadError as exc:
            if "HTML page" in str(exc) or attempt == attempts:
                raise
            log.warning("%s (attempt %s/%s)", exc, attempt, attempts)
        except httpx.HTTPError as exc:  # connection dropped mid-transfer
            if attempt == attempts:
                raise ApiError(f"download failed: {exc}") from exc
            log.warning("download of %s interrupted (%s), retrying", file.name, exc)
        _sleep(2**attempt)
    raise AssertionError("unreachable")


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
        mime_type=file.mime_type,
        description=file.description,
        content_id=file.content_id,
    )


def _label(action: Action) -> str:
    return action.file.name if action.file else str(action.dest)
