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

from .api import ApiError, AuthFailed
from .fsutil import atomic_writer, local_stat, move_no_clobber, sha256_file
from .models import RemoteFile
from .planner import Action, CoursePlan, Kind, is_locally_modified
from .postprocess import PostprocessError, Unlock, extract_zip, unlock_pdf
from .state import FileRecord, State

log = logging.getLogger(__name__)

Downloader = Callable[[str, Callable[[bytes], None]], None]


@dataclass
class Outcome:
    done: list[Action] = field(default_factory=list)
    failed: list[tuple[Action, str]] = field(default_factory=list)
    extracted: list[Path] = field(default_factory=list)
    unlocked: list[Path] = field(default_factory=list)  # PDF password removed
    still_locked: list[tuple[Path, str]] = field(default_factory=list)  # kept as downloaded


def execute(
    plan: CoursePlan,
    state: State,
    download: Downloader,
    run_id: int,
    on_progress: Callable[[Action], None] | None = None,
    pdf_password: str | None = None,
) -> Outcome:
    """Apply ``plan``. With ``pdf_password``, new files of the course that are PDFs
    locked with exactly this password are stored without it.

    One failing file never stops the others, whatever the error; only a rejected
    login (``AuthFailed``) ends the run, so a wrong password is not retried for
    every file (that could lock the account).
    """
    outcome = Outcome()
    for action in plan.actions:
        if action.kind in (Kind.SKIP, Kind.ERROR):
            continue
        try:
            _apply(action, state, download, run_id, plan.max_bytes)
            if pdf_password and action.kind in UNLOCKS:
                _unlock(action, state, pdf_password, outcome)
        except AuthFailed:
            raise
        except Exception as exc:  # KeyboardInterrupt still stops the run
            if isinstance(exc, (ApiError, OSError)):
                log.error("failed: %s -> %s: %s", _label(action), action.dest, exc)
                message = str(exc)
            else:
                log.exception("unexpected error: %s -> %s", _label(action), action.dest)
                message = f"unexpected error: {type(exc).__name__}: {exc}"
            outcome.failed.append((action, message))
            if action.file is not None:
                state.record_failure(action.file.id, message)
            continue
        if action.file is not None and action.transfers:
            state.clear_failure(action.file.id)
        outcome.done.append(action)
        if on_progress:
            on_progress(action)
        if action.unzips:
            _unzip(action, state, run_id, outcome)
    return outcome


UNLOCKS = (Kind.DOWNLOAD, Kind.NEW_VERSION, Kind.OVERWRITE, Kind.ADOPT)


def _unlock(action: Action, state: State, password: str, outcome: Outcome) -> None:
    """Remove the course's PDF password from a file just written or adopted."""
    assert action.dest is not None and action.file is not None
    record = state.get(action.file.id)
    if record is not None and Path(record.local_path) == action.dest:
        unlock_record(state, record, password, outcome)
    # else nothing was written (identical content) or the file is elsewhere


def unlock_record(state: State, record: FileRecord, password: str, outcome: Outcome) -> None:
    """Remove the PDF password from a tracked file, if it is a PDF locked with it.

    The record keeps the hash of the downloaded bytes (to recognise identical
    re-uploads) and gets the hash of the unlocked file (to detect local edits).
    """
    path = Path(record.local_path)
    st = local_stat(path)
    if st is None or st.dataless:
        return  # missing, or only in iCloud: never downloaded just for this
    if is_locally_modified(record, st, path):
        return  # edited (e.g. annotated) since the sync: never rewritten
    try:
        result = unlock_pdf(path, password)
    except PostprocessError as exc:
        log.warning("%s", exc)
        outcome.still_locked.append((path, str(exc)))
        return
    if result is Unlock.WRONG_PASSWORD:
        log.warning("%s: %s, kept it locked", path, result)
        outcome.still_locked.append((path, str(result)))
        return
    if result is not Unlock.DECRYPTED:
        return
    os.utime(path, (st.mtime, st.mtime))
    record.local_size = path.stat().st_size
    record.local_mtime = st.mtime
    record.local_sha256 = sha256_file(path)
    state.upsert(record)
    outcome.unlocked.append(path)
    log.info("unlocked %s", path)


def unlock_course(state: State, course_id: str, password: str) -> Outcome:
    """Unlock the PDFs already synced for a course (after its password was set)."""
    outcome = Outcome()
    for record in state.for_course(course_id).values():
        if record.local_sha256 is None:  # unlocked files are never touched again
            unlock_record(state, record, password, outcome)
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


def _apply(
    action: Action,
    state: State,
    download: Downloader,
    run_id: int,
    max_bytes: int | None = None,
) -> None:
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
            previous.local_sha256,
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
            previous.local_sha256,
        )
        log.info("rename %s -> %s", previous.local_path, action.dest)
        return

    # DOWNLOAD / NEW_VERSION / OVERWRITE
    if action.kind is not Kind.OVERWRITE and action.dest.exists():
        # Plan is stale (file appeared meanwhile): never clobber.
        raise OSError(f"refusing to overwrite unexpected file {action.dest}")
    if action.kind is Kind.OVERWRITE:
        assert previous is not None
        _check_unchanged(previous, action.dest)  # the plan may be old (UI preview)
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
    dest = action.dest
    before_replace = (
        (lambda: _check_unchanged(previous, dest))
        if action.kind is Kind.OVERWRITE and previous is not None
        else None
    )
    sha, written = fetch_verified(
        file,
        action.dest,
        download,
        unless_sha=known,
        before_replace=before_replace,
        max_bytes=max_bytes,
    )
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
            previous.local_sha256,
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
    local_sha: str | None = None,
) -> None:
    state.upsert(_record(action, file, path, size, mtime, sha, version, local_sha))
    previous = action.previous
    if previous is not None and previous.file_id != file.id:  # re-upload replaced it
        state.delete_file_record(previous.file_id)


def _check_unchanged(previous: FileRecord, path: Path) -> None:
    """Raise if the local copy was edited since we wrote it (checked at run time)."""
    st = local_stat(path)
    if st is not None and is_locally_modified(previous, st, path):
        raise LocalChangeError(
            f"{path.name} was changed locally since the preview; not overwritten "
            "(the next sync keeps it and saves the new version next to it)"
        )


class LocalChangeError(OSError):
    """The file to overwrite was edited after the plan was made."""


_sleep = time.sleep  # replaced in tests


class DownloadError(OSError):
    """The received content is not the file we expected."""


class TooLargeError(DownloadError):
    """The server sends far more than it announced, or more than the size limit."""


def size_limit(file: RemoteFile, max_bytes: int | None) -> int | None:
    """Most bytes accepted for ``file``: a bit more than announced (Stud.IP's size is
    sometimes slightly off), never more than the configured size limit."""
    limits = [max_bytes] if max_bytes else []
    if file.size > 0:
        limits.append(file.size + max(file.size // 10, 1 << 20))
    return min(limits) if limits else None


class _Guard:
    """Counts bytes, stops oversized transfers and rejects HTML pages served instead
    of a file."""

    def __init__(
        self, file: RemoteFile, sink: Callable[[bytes], object], limit: int | None = None
    ) -> None:
        self.file = file
        self.sink = sink
        self.size = 0
        self.limit = limit

    def write(self, chunk: bytes) -> None:
        if self.size == 0 and chunk and not _expects_html(self.file):
            head = chunk[:512].lstrip().lower()
            if head.startswith((b"<!doctype html", b"<html")):
                raise DownloadError(
                    "received an HTML page instead of the file "
                    "(expired login, missing permission or a link to a website?)"
                )
        self.size += len(chunk)
        if self.limit is not None and self.size > self.limit:
            raise TooLargeError(
                f"Stud.IP sends more than {self.limit // (1 << 20) or 1} MB for "
                f"{self.file.name} (announced: {self.file.size} bytes); stopped"
            )
        self.sink(chunk)


def _expects_html(file: RemoteFile) -> bool:
    return file.name.lower().endswith((".html", ".htm")) or "html" in file.mime_type.lower()


def fetch_verified(
    file: RemoteFile,
    dest: Path,
    download: Downloader,
    attempts: int = 3,
    unless_sha: str | None = None,
    before_replace: Callable[[], None] | None = None,
    max_bytes: int | None = None,
) -> tuple[str, bool]:
    """Download into ``dest`` atomically; return (sha256, written).

    Interrupted transfers and size mismatches are retried. If the size reported
    by Stud.IP is wrong but two complete downloads agree, the file is accepted.
    With ``unless_sha``, identical content is discarded instead of written.
    ``before_replace`` runs right before ``dest`` is replaced and may raise to keep it.
    A transfer far bigger than announced, or bigger than ``max_bytes``, is stopped
    at once and not retried.
    """
    mismatch: int | None = None
    for attempt in range(1, attempts + 1):
        try:

            def commit(sha: str) -> bool:
                if unless_sha and sha == unless_sha:
                    return False
                if before_replace is not None:
                    before_replace()
                return True

            with atomic_writer(dest, commit) as (fh, digest):
                guard = _Guard(file, fh.write, size_limit(file, max_bytes))
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
            if isinstance(exc, TooLargeError) or "HTML page" in str(exc) or attempt == attempts:
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
    local_sha: str | None = None,
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
        local_path=str(path),  # as on disk; compare via path_key()
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
        local_sha256=local_sha,
    )


def _label(action: Action) -> str:
    return action.file.name if action.file else str(action.dest)
