"""Compute what a sync would do, without doing it.

The planner only *reads* the local filesystem (stat, directory listings and,
for files present locally, hashes). Dry run prints its plan; a real run hands
the same plan to the executor. That is what keeps both modes identical.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .config import Config, CourseConfig, ExcludeConfig
from .fsutil import (
    LocalStat,
    local_stat,
    nfc,
    path_key,
    sanitize_filename,
    sha256_file,
    walk_files,
    with_suffix_number,
)
from .models import RemoteFile
from .state import FileRecord


class Kind(StrEnum):
    DOWNLOAD = "download"  # new file
    ADOPT = "adopt"  # identical file already on disk: record only
    NEW_VERSION = "new-version"  # remote changed, keep old, write _vN next to it
    OVERWRITE = "overwrite"  # remote changed, replace unmodified local copy
    MKDIR = "mkdir"
    SKIP = "skip"
    ERROR = "error"


class SkipReason(StrEnum):
    EXCLUDED = "excluded"
    TOO_LARGE = "too large"
    NOT_DOWNLOADABLE = "not downloadable"
    UPDATE_SKIPPED = "update skipped (policy)"
    LOCAL_MODIFIED = "local copy modified"
    LOCAL_MISSING = "local copy moved or deleted"
    CONFLICT = "name conflict (policy: skip)"


@dataclass(frozen=True)
class Route:
    """Where a file goes, relative to the course folder."""

    subdir: str
    name: str
    rule_id: str | None = None
    by_default: bool = False


@dataclass
class Action:
    kind: Kind
    course_id: str
    file: RemoteFile | None = None
    dest: Path | None = None
    route: Route | None = None
    previous: FileRecord | None = None
    version: int = 1
    reason: str = ""
    note: str = ""

    @property
    def transfers(self) -> bool:
        return self.kind in (Kind.DOWNLOAD, Kind.NEW_VERSION, Kind.OVERWRITE)


@dataclass
class CoursePlan:
    course: CourseConfig
    course_dir: Path
    actions: list[Action] = field(default_factory=list)
    unchanged: int = 0
    remote_deleted: list[FileRecord] = field(default_factory=list)


Router = Callable[[RemoteFile], Route]


def default_router(cfg: Config, course: CourseConfig) -> Router:
    target = cfg.course_default_target(course)

    def route(file: RemoteFile) -> Route:
        return Route(subdir=target, name=sanitize_filename(file.name), by_default=True)

    return route


def exclusion_reason(file: RemoteFile, rules: ExcludeConfig) -> SkipReason | None:
    if not file.downloadable:
        return SkipReason.NOT_DOWNLOADABLE
    name = nfc(file.name).casefold()
    if any(fnmatch.fnmatchcase(name, nfc(g).casefold()) for g in rules.globs):
        return SkipReason.EXCLUDED
    if any(fnmatch.fnmatchcase(file.mime_type.lower(), m.lower()) for m in rules.mime):
        return SkipReason.EXCLUDED
    if rules.max_size_mb is not None and file.size > rules.max_size_mb * 1024 * 1024:
        return SkipReason.TOO_LARGE
    return None


class _Claims:
    """Paths that are taken: existing files, state entries and this run's targets."""

    def __init__(self, existing: list[Path]) -> None:
        self._taken = {path_key(p) for p in existing}

    def taken(self, path: Path) -> bool:
        return path_key(path) in self._taken

    def claim(self, path: Path) -> None:
        self._taken.add(path_key(path))


def is_locally_modified(record: FileRecord, st: LocalStat, path: Path) -> bool:
    """Compare a local file against what we wrote. Never reads evicted iCloud files."""
    if st.size == record.local_size and abs(st.mtime - record.local_mtime) < 1:
        return False
    if st.size != record.local_size:
        return True
    if st.dataless or record.sha256 is None:
        return True  # cannot verify without downloading: assume modified (safe side)
    return sha256_file(path) != record.sha256


def plan_course(
    cfg: Config,
    course: CourseConfig,
    remote_files: list[RemoteFile],
    records: dict[str, FileRecord],
    *,
    router: Router | None = None,
    include_large: bool = False,
) -> CoursePlan:
    course_dir = cfg.course_dir(course)
    plan = CoursePlan(course=course, course_dir=course_dir)
    route_file = router or default_router(cfg, course)
    excludes = cfg.course_exclude(course)
    if include_large:
        excludes = ExcludeConfig(excludes.globs, excludes.mime, None)

    existing = list(walk_files(course_dir))
    by_name: dict[str, list[Path]] = {}
    for p in existing:
        by_name.setdefault(nfc(p.name).casefold(), []).append(p)
    claims = _Claims(existing + [Path(r.local_path) for r in records.values()])
    adopted: set[str] = set(path_key(r.local_path) for r in records.values())
    planned_dirs: set[str] = set()

    for file in sorted(remote_files, key=lambda f: (f.folder_path, nfc(f.name))):
        reason = exclusion_reason(file, excludes)
        if reason:
            plan.actions.append(Action(Kind.SKIP, course.id, file, reason=reason))
            continue

        record = records.get(file.id)
        if record is not None:
            _plan_known(cfg, plan, file, record, claims)
            continue

        route = route_file(file)
        dest = course_dir / route.subdir / route.name

        # Already on disk (e.g. downloaded by hand earlier)? Adopt instead of duplicating.
        match = _find_identical(by_name, route.name, file.size, adopted)
        if match is not None:
            adopted.add(path_key(match))
            plan.actions.append(
                Action(
                    Kind.ADOPT,
                    course.id,
                    file,
                    dest=match,
                    route=route,
                    note="already present locally",
                )
            )
            continue

        if not _ensure_dir(cfg, plan, dest.parent, planned_dirs, file):
            continue

        if claims.taken(dest):
            if cfg.on_conflict == "skip":
                plan.actions.append(
                    Action(Kind.SKIP, course.id, file, dest=dest, reason=SkipReason.CONFLICT)
                )
                continue
            if cfg.on_conflict == "error":
                plan.actions.append(
                    Action(
                        Kind.ERROR, course.id, file, dest=dest, reason=f"target exists: {dest.name}"
                    )
                )
                continue
            original = dest
            n = 2
            while claims.taken(dest := with_suffix_number(original, n, "_{n}")):
                n += 1
            note = f"name taken, renamed from {original.name}"
        else:
            note = ""
        claims.claim(dest)
        plan.actions.append(
            Action(Kind.DOWNLOAD, course.id, file, dest=dest, route=route, note=note)
        )

    remote_ids = {f.id for f in remote_files}
    plan.remote_deleted = [r for fid, r in records.items() if fid not in remote_ids]
    return plan


def _plan_known(
    cfg: Config, plan: CoursePlan, file: RemoteFile, record: FileRecord, claims: _Claims
) -> None:
    if record.chdate == file.chdate.isoformat() and record.filesize == file.size:
        plan.unchanged += 1
        return

    path = Path(record.local_path)
    st = local_stat(path)
    if st is None:
        plan.actions.append(
            Action(
                Kind.SKIP,
                plan.course.id,
                file,
                dest=path,
                previous=record,
                reason=SkipReason.LOCAL_MISSING,
            )
        )
        return
    if cfg.on_update == "skip":
        plan.actions.append(
            Action(
                Kind.SKIP,
                plan.course.id,
                file,
                dest=path,
                previous=record,
                reason=SkipReason.UPDATE_SKIPPED,
            )
        )
        return

    modified = is_locally_modified(record, st, path)
    if cfg.on_update == "overwrite" and not modified:
        plan.actions.append(
            Action(
                Kind.OVERWRITE,
                plan.course.id,
                file,
                dest=path,
                previous=record,
                version=record.version,
            )
        )
        return

    # "version" policy, or "overwrite" that must not clobber local edits.
    version = record.version + 1
    base = _version_base(path, record.version)
    while claims.taken(dest := with_suffix_number(base, version, "_v{n}")):
        version += 1
    claims.claim(dest)
    note = "local copy modified, kept it" if modified else ""
    plan.actions.append(
        Action(
            Kind.NEW_VERSION,
            plan.course.id,
            file,
            dest=dest,
            previous=record,
            version=version,
            note=note,
        )
    )


def _version_base(path: Path, version: int) -> Path:
    """Strip an existing _vN suffix so versions count up as name_v2, name_v3 ..."""
    if version <= 1:
        return path
    suffix = f"_v{version}"
    stem, dot, ext = path.name.rpartition(".")
    if dot and stem.endswith(suffix):
        return path.with_name(f"{stem[: -len(suffix)]}.{ext}")
    if not dot and path.name.endswith(suffix):
        return path.with_name(path.name[: -len(suffix)])
    return path


def _find_identical(
    by_name: dict[str, list[Path]], name: str, size: int, adopted: set[str]
) -> Path | None:
    for candidate in by_name.get(nfc(name).casefold(), []):
        if path_key(candidate) in adopted:
            continue
        st = local_stat(candidate)
        if st is not None and st.size == size:
            return candidate
    return None


def _ensure_dir(
    cfg: Config, plan: CoursePlan, directory: Path, planned: set[str], file: RemoteFile
) -> bool:
    if directory.is_dir() or path_key(directory) in planned:
        return True
    if cfg.create_missing_dirs:
        planned.add(path_key(directory))
        plan.actions.append(Action(Kind.MKDIR, plan.course.id, dest=directory))
        return True
    hint = ""
    if " / " in str(directory.relative_to(plan.course_dir)):
        hint = " (on macOS a Finder name 'A / B' is 'A : B' on disk)"
    plan.actions.append(
        Action(
            Kind.ERROR,
            plan.course.id,
            file,
            dest=directory,
            reason=f"target folder does not exist{hint}",
        )
    )
    return False
