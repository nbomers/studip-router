"""Compute what a sync would do, without doing it.

The planner only *reads* the local filesystem (stat, directory listings and,
for files present locally, hashes). Dry run prints its plan; a real run hands
the same plan to the executor. That is what keeps both modes identical.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
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
from .rename import RenameError, target_name
from .rules import first_match
from .state import FileRecord
from .suggest import auto_target, local_subdirs


class Kind(StrEnum):
    DOWNLOAD = "download"  # new file
    ADOPT = "adopt"  # identical file already on disk: record only
    NEW_VERSION = "new-version"  # remote changed, keep old, write _vN next to it
    OVERWRITE = "overwrite"  # remote changed, replace unmodified local copy
    REFRESH = "refresh"  # only Stud.IP metadata changed (name, folder, date): record only
    MOVE = "rename"  # file renamed on Stud.IP: rename the unmodified local copy too
    MKDIR = "mkdir"
    SKIP = "skip"
    ERROR = "error"


class SkipReason(StrEnum):
    EXCLUDED = "excluded"
    TOO_LARGE = "too large"
    NOT_DOWNLOADABLE = "not downloadable"
    UPDATE_SKIPPED = "update skipped (policy)"
    LOCAL_MODIFIED = "local copy modified"
    CONFLICT = "name conflict (policy: skip)"
    RULE = "skipped by rule"
    IGNORED = "ignored by you"


@dataclass(frozen=True)
class Route:
    """Where a file goes, relative to the course folder."""

    subdir: str
    name: str
    rule_id: str | None = None
    by_default: bool = False
    skip: bool = False
    unzip: bool = False


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

    @property
    def changes_disk(self) -> bool:
        return self.kind not in (Kind.SKIP, Kind.ERROR)

    @property
    def unzips(self) -> bool:
        """True if the archive will be extracted next to itself after this action."""
        if self.route is None or not self.route.unzip or self.dest is None:
            return False
        if self.dest.suffix.lower() != ".zip":
            return False
        if self.kind is Kind.ADOPT:
            return not self.dest.with_suffix("").exists()
        return self.transfers


@dataclass
class CoursePlan:
    course: CourseConfig
    course_dir: Path
    actions: list[Action] = field(default_factory=list)
    unchanged: int = 0
    remote_deleted: list[FileRecord] = field(default_factory=list)  # newly deleted only
    reappeared: list[str] = field(default_factory=list)  # ids marked gone that are back


Router = Callable[[RemoteFile], Route]


def build_router(cfg: Config, course: CourseConfig, semester: str = "") -> Router:
    """Route by the course's rules (first match wins), else to the default target.

    Raises RenameError for templates that fail on a concrete file.
    """
    default_target = cfg.course_default_target(course)
    options = cfg.course_rename(course)
    unzip = cfg.course_unzip(course)
    label = course.name or course.target
    subdirs = local_subdirs(cfg.course_dir(course)) if cfg.auto_route else []

    def route(file: RemoteFile) -> Route:
        rule, regex_match = first_match(course.rules, file)
        if rule is None:
            name = target_name(file, None, options)
            if found := auto_target(file.folder_path, subdirs):
                category, target = found
                return Route(target, name, rule_id=f"auto:{category}", unzip=unzip)
            return Route(default_target, name, by_default=True, unzip=unzip)
        if rule.skip:
            return Route(
                "",
                sanitize_filename(file.name, mime_type=file.mime_type),
                rule_id=rule.id,
                skip=True,
            )
        name = target_name(
            file, rule.rename, options, course=label, semester=semester, match=regex_match
        )
        assert rule.target is not None
        return Route(
            rule.target, name, rule_id=rule.id, unzip=unzip if rule.unzip is None else rule.unzip
        )

    return route


def exclusion_reason(file: RemoteFile, rules: ExcludeConfig) -> SkipReason | None:
    if not file.downloadable:
        return SkipReason.NOT_DOWNLOADABLE
    name = nfc(file.name).casefold()
    if any(fnmatch.fnmatchcase(name, nfc(g).casefold()) for g in rules.globs):
        return SkipReason.EXCLUDED
    if any(fnmatch.fnmatchcase(file.mime_type.lower(), m.lower()) for m in rules.mime):
        return SkipReason.EXCLUDED
    if rules.max_size_mb and file.size > rules.max_size_mb * 1024 * 1024:  # 0: no limit
        return SkipReason.TOO_LARGE
    folders = [nfc(p).casefold() for p in file.folder_path.split("/") if p]
    if any(fnmatch.fnmatchcase(f, nfc(g).casefold()) for f in folders for g in rules.folders):
        return SkipReason.EXCLUDED
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
    if st.dataless or record.local_digest is None:
        return True  # cannot verify without downloading: assume modified (safe side)
    return sha256_file(path) != record.local_digest


def plan_course(
    cfg: Config,
    course: CourseConfig,
    remote_files: list[RemoteFile],
    records: dict[str, FileRecord],
    *,
    router: Router | None = None,
    include_large: bool = False,
    fresh_since: datetime | None = None,
    create_course_dir: bool = False,
    ignored: set[str] | None = None,
    failures: dict[str, tuple[int, str, str]] | None = None,
) -> CoursePlan:
    """Plan one course.

    ``fresh_since``: local files with the same name but a different size that
    were modified after this moment (e.g. downloaded by hand this semester) are
    treated as an older version of the remote file. ``None`` disables this.
    ``ignored``: file ids the user never wants. ``failures``: files that keep
    failing are skipped for a day after three attempts.
    """
    course_dir = cfg.course_dir(course)
    plan = CoursePlan(course=course, course_dir=course_dir)
    route_file = router or build_router(cfg, course)
    excludes = cfg.course_exclude(course)
    if include_large:
        excludes = ExcludeConfig(excludes.globs, excludes.mime, None)
    ignored = ignored or set()
    failures = failures or {}
    planned_dirs: set[str] = set()

    if not course_dir.is_dir():
        if not create_course_dir:
            plan.actions.append(
                Action(
                    Kind.ERROR,
                    course.id,
                    dest=course_dir,
                    reason="course folder does not exist (renamed or moved? update the target)",
                )
            )
            return plan
        planned_dirs.add(path_key(course_dir))
        plan.actions.append(Action(Kind.MKDIR, course.id, dest=course_dir))

    existing = list(walk_files(course_dir))
    by_name: dict[str, list[Path]] = {}
    for p in existing:
        by_name.setdefault(name_key(p.name), []).append(p)
    # "a_v2.pdf" also under "a.pdf", but only next to an existing "a.pdf": that is a
    # version chain this tool wrote (state lost). A lone "Blatt_01.pdf" is no
    # version of "Blatt.pdf".
    present = {path_key(p) for p in existing}
    by_base: dict[str, list[Path]] = {}
    for p in existing:
        base = _unsuffixed(p.name)
        if base is not None and path_key(p.with_name(base)) in present:
            by_base.setdefault(name_key(base), []).append(p)
    claims = _Claims(existing + [Path(r.local_path) for r in records.values()])
    adopted: set[str] = set(path_key(r.local_path) for r in records.values())

    remote_ids = {f.id for f in remote_files}
    # Deleted on Stud.IP: a new upload with the same name replaces them (re-upload).
    replaced_by_name: dict[str, FileRecord] = {}
    for fid, r in records.items():
        if fid not in remote_ids:
            replaced_by_name.setdefault(name_key(r.remote_name), r)
    replaced: set[str] = set()
    plan.reappeared = [fid for fid, r in records.items() if r.gone and fid in remote_ids]
    ctx = _Ctx(cfg, plan, claims, route_file, by_name, by_base, adopted, planned_dirs)

    for file in sorted(remote_files, key=lambda f: (f.folder_path, nfc(f.name))):
        reason: str | None = exclusion_reason(file, excludes)
        if file.id in ignored:
            reason = SkipReason.IGNORED
        elif (backoff := _backoff(failures.get(file.id))) is not None:
            reason = backoff
        if reason:
            plan.actions.append(Action(Kind.SKIP, course.id, file, reason=reason))
            continue

        record = records.get(file.id)
        old = None if record else replaced_by_name.get(name_key(file.name))
        if (
            old is not None
            and old.file_id not in replaced
            and local_stat(Path(old.local_path)) is not None
        ):
            replaced.add(old.file_id)  # deleted and uploaded again: a new version of it
            record = old
        if record is not None:
            _plan_known(ctx, file, record)
            continue

        try:
            route = route_file(file)
        except RenameError as exc:
            plan.actions.append(Action(Kind.ERROR, course.id, file, reason=str(exc)))
            continue
        if route.skip:
            plan.actions.append(
                Action(
                    Kind.SKIP,
                    course.id,
                    file,
                    route=route,
                    reason=f"{SkipReason.RULE} {route.rule_id}",
                )
            )
            continue
        dest = course_dir / route.subdir / route.name
        # Hand-downloaded copies carry the original name, not the renamed one.
        names = [route.name, sanitize_filename(file.name, portable=False), file.name]

        # Already on disk (e.g. downloaded by hand earlier)? Adopt instead of duplicating.
        match = _find_local(by_name, names, file.size, adopted, None) or _find_local(
            by_base, names, file.size, adopted, None
        )
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

        # Same name, different content, fetched by hand this semester: an older version.
        older = fresh_since and _find_local(by_name, names, None, adopted, fresh_since)
        if older:
            adopted.add(path_key(older))
            if cfg.on_update == "skip":
                plan.actions.append(
                    Action(Kind.SKIP, course.id, file, dest=older, reason=SkipReason.UPDATE_SKIPPED)
                )
                continue
            version = 2
            while claims.taken(dest := with_suffix_number(older, version, "_v{n}")):
                version += 1
            claims.claim(dest)
            plan.actions.append(
                Action(
                    Kind.NEW_VERSION,
                    course.id,
                    file,
                    dest=dest,
                    route=route,
                    version=version,
                    note=f"differs from local {older.name}, kept it",
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

    plan.remote_deleted = [
        r
        for fid, r in records.items()
        if fid not in remote_ids and fid not in replaced and not r.gone
    ]
    return plan


@dataclass
class _Ctx:
    cfg: Config
    plan: CoursePlan
    claims: _Claims
    route_file: Router
    by_name: dict[str, list[Path]]
    by_base: dict[str, list[Path]]
    adopted: set[str]
    planned_dirs: set[str]


MAX_FAILURES = 3
RETRY_AFTER = timedelta(hours=24)


def _backoff(failure: tuple[int, str, str] | None) -> str | None:
    if failure is None or failure[0] < MAX_FAILURES:
        return None
    count, error, last_try = failure
    try:
        last = datetime.fromisoformat(last_try)
    except ValueError:
        return None
    if datetime.now(UTC) - last >= RETRY_AFTER:
        return None
    return f"failed {count}x ({error[:60]}), retried after 24 h"


def same_time(stored: str, remote: datetime) -> bool:
    """Compare instants, not strings: "+02:00" vs "Z" formatting must not matter."""
    try:
        return datetime.fromisoformat(stored.replace("Z", "+00:00")) == remote
    except ValueError:
        return False


_SUFFIXED = re.compile(r"^(.+?)_v\d{1,3}(\.[^.]+)?$")


def _unsuffixed(name: str) -> str | None:
    """ "a_v2.pdf" -> "a.pdf" (the names this tool creates for versions)."""
    m = _SUFFIXED.match(name)
    return f"{m.group(1)}{m.group(2) or ''}" if m else None


def _plan_known(ctx: _Ctx, file: RemoteFile, record: FileRecord) -> None:
    cfg, plan = ctx.cfg, ctx.plan
    path = Path(record.local_path)
    st = local_stat(path)  # stat() never pulls a file out of iCloud
    if st is None:
        _plan_missing(ctx, file, record)
        return
    same_file = record.file_id == file.id
    if same_file and same_time(record.chdate, file.chdate) and record.filesize == file.size:
        plan.unchanged += 1
        return

    try:
        route = ctx.route_file(file)
    except RenameError:
        route = None

    content_same = (
        same_file
        and record.filesize == file.size
        and bool(record.content_id)
        and record.content_id == file.content_id
    )
    if content_same:  # renamed, moved or re-described on Stud.IP; the content is the same
        if (
            cfg.follow_renames
            and route is not None
            and nfc(record.remote_name) != nfc(file.name)
            and record.version == 1
            and not is_locally_modified(record, st, path)
        ):
            dest = path.with_name(route.name)
            if path_key(dest) != path_key(path) and not ctx.claims.taken(dest):
                ctx.claims.claim(dest)
                plan.actions.append(
                    Action(
                        Kind.MOVE,
                        plan.course.id,
                        file,
                        dest=dest,
                        route=route,
                        previous=record,
                        note=f"renamed on Stud.IP, was {path.name}",
                    )
                )
                return
        plan.actions.append(
            Action(
                Kind.REFRESH,
                plan.course.id,
                file,
                dest=path,
                route=route,
                previous=record,
                version=record.version,
                note="only details changed on Stud.IP",
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
    reupload = "" if same_file else "uploaded again on Stud.IP; "
    if cfg.on_update == "overwrite" and not modified:
        plan.actions.append(
            Action(
                Kind.OVERWRITE,
                plan.course.id,
                file,
                dest=path,
                route=route,
                previous=record,
                version=record.version,
                note=reupload.rstrip("; "),
            )
        )
        return

    # "version" policy, or "overwrite" that must not clobber local edits.
    version = record.version + 1
    base = _version_base(path, record.version)
    while ctx.claims.taken(dest := with_suffix_number(base, version, "_v{n}")):
        version += 1
    ctx.claims.claim(dest)
    note = reupload + ("local copy modified, kept it" if modified else "")
    plan.actions.append(
        Action(
            Kind.NEW_VERSION,
            plan.course.id,
            file,
            dest=dest,
            route=route,
            previous=record,
            version=version,
            note=note.rstrip("; "),
        )
    )


def _plan_missing(ctx: _Ctx, file: RemoteFile, record: FileRecord) -> None:
    """A tracked file is gone locally: follow it if it was moved, else download it again."""
    path = Path(record.local_path)
    names = [path.name, sanitize_filename(file.name, portable=False), file.name]
    moved = _find_local(ctx.by_name, names, record.local_size, ctx.adopted, None)
    if moved is not None and same_time(record.chdate, file.chdate):
        ctx.adopted.add(path_key(moved))
        ctx.plan.actions.append(
            Action(
                Kind.ADOPT,
                ctx.plan.course.id,
                file,
                dest=moved,
                previous=record,
                version=record.version,
                note=f"moved locally from {path.name}",
            )
        )
        return
    if not _ensure_dir(ctx.cfg, ctx.plan, path.parent, ctx.planned_dirs, file):
        return
    version = record.version
    base = _version_base(path, record.version)
    if version > 1 and not base.exists() and not ctx.claims.taken(base):
        path, version = base, 1  # the original is gone too: restore under its own name
        ctx.claims.claim(base)
    ctx.plan.actions.append(
        Action(
            Kind.DOWNLOAD,
            ctx.plan.course.id,
            file,
            dest=path,
            previous=record,
            version=version,
            note="deleted locally, restoring",
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


def name_key(name: str) -> str:
    """Loose file name identity: NFC, case-insensitive; spaces, "_", "-", ":" alike.

    Browsers and Stud.IP replace spaces and special characters when files are
    downloaded by hand, so "Homework_1.pdf" on disk is "Homework 1.pdf" in the API.
    """
    return re.sub(r"[\s_:\-]+", "_", nfc(name).casefold())


def _find_local(
    by_name: dict[str, list[Path]],
    names: list[str],
    size: int | None,
    adopted: set[str],
    modified_after: datetime | None,
) -> Path | None:
    """A not-yet-tracked local file with this name and size (or any size, if None)."""
    keys = dict.fromkeys(name_key(n) for n in names)
    for candidate in (c for k in keys for c in by_name.get(k, [])):
        if path_key(candidate) in adopted:
            continue
        st = local_stat(candidate)
        if st is None:
            continue
        # size -1: old-style iCloud placeholder, size unknown - trust the name
        if size is not None and st.size not in (size, -1):
            continue
        if modified_after is not None and st.mtime < modified_after.timestamp():
            continue
        return candidate
    return None


def _ensure_dir(
    cfg: Config, plan: CoursePlan, directory: Path, planned: set[str], file: RemoteFile
) -> bool:
    if directory.is_dir() or path_key(directory) in planned:
        return True
    default = cfg.course_default_target(plan.course)
    # The default folder is the tool's own catch-all and is created when needed,
    # unless its name is a Finder spelling ("A / B") that would nest wrong folders.
    is_default = path_key(directory) == path_key(plan.course_dir / default) and " / " not in default
    if cfg.create_missing_dirs or is_default:
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
