"""Semester change: set up the courses of a new semester and archive the old ones.

Twice a year the courses change. Until 0.4.2 that meant knowing the config key
``sync.root`` and turning old courses off one by one. This module finds the
change (new courses of a semester, set-up courses that do not run in it) and
applies it in one config write:

- an optional new root folder for the semester (a sibling of the current one);
  set-up courses keep their folders, their targets become absolute;
- a folder for each new course, with the usual subfolders;
- archived courses: no longer synced (``exclude = true``), optionally with their
  files made read-only.

Courses that keep running (several semesters, no end) are never touched.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths
from .config import DEFAULT_COURSE_FOLDERS, Config, CourseConfig, unsafe_folder
from .config_edit import edit_courses
from .fsutil import sanitize_filename
from .models import Course, Semester
from .wizard import (
    course_folder_name,
    create_course_folder,
    detect_course_folders,
    home_path,
    local_course_folders,
    propose_target,
)

UPCOMING_DAYS = 60  # the same window in which new courses show up (sync.UPCOMING_DAYS)


@dataclass(frozen=True)
class OldCourse:
    config: CourseConfig
    course: Course
    ended: bool  # its last semester is over: archived by default

    @property
    def label(self) -> str:
        return self.config.name or self.course.title or self.config.target


@dataclass
class SemesterChange:
    semester: Semester  # the semester the new courses belong to
    new_courses: list[Course]
    old_courses: list[OldCourse]
    root: Path  # the current root
    proposed_root: Path | None  # a folder for the new semester, None: keep the root
    subfolders: tuple[str, ...]
    targets: dict[str, str] = field(default_factory=dict)  # new course id -> folder name
    in_use: set[Path] = field(default_factory=set)  # folders of set-up courses

    def target_root(self, new_root: bool) -> Path:
        return self.proposed_root if new_root and self.proposed_root else self.root


# -- finding the change ---------------------------------------------------------------


def _current(semesters: list[Semester], now: datetime) -> Semester | None:
    for sem in semesters:
        if sem.contains(now):
            return sem
    past = [s for s in semesters if s.end < now]  # between two semesters
    return max(past, key=lambda s: s.end) if past else None


def _upcoming(semesters: list[Semester], current: Semester, now: datetime) -> Semester | None:
    later = [s for s in semesters if s.start > current.start]
    if not later:
        return None
    nxt = min(later, key=lambda s: s.start)
    return nxt if (nxt.start - now).days <= UPCOMING_DAYS else None


def _starts_in(course: Course, semester: Semester) -> bool:
    return course.start_semester is not None and course.start_semester.id == semester.id


def find_change(
    cfg: Config,
    courses: list[Course],
    semesters: list[Semester],
    now: datetime,
    kept: set[str] | None = None,
) -> SemesterChange | None:
    """The semester change to offer, or None.

    Offered when set-up courses do not run in the semester of the newest courses
    and either new courses are waiting or one of the old courses has ended.
    New courses alone (mid-semester) are the usual "new course" step instead.
    ``kept`` are ended courses the user chose to keep syncing: not asked again.
    """
    kept = kept or set()
    current = _current(semesters, now)
    if current is None:
        return None
    upcoming = _upcoming(semesters, current, now)

    def new_in(sem: Semester) -> list[Course]:
        return [c for c in courses if c.id not in cfg.courses and _starts_in(c, sem)]

    semester = upcoming if upcoming and new_in(upcoming) else current
    new = sorted(new_in(semester), key=lambda c: c.title.casefold())
    old: list[OldCourse] = []
    for course in courses:
        configured = cfg.courses.get(course.id)
        if configured is None or configured.exclude or course.id in kept:
            continue
        if course.runs_in(semester):
            continue
        ended = course.end_semester is not None and course.end_semester.end < now
        old.append(OldCourse(configured, course, ended))
    old.sort(key=lambda o: o.label.casefold())
    if not old or not (new or any(o.ended for o in old)):
        return None

    proposed = propose_root(cfg.root, semester.title) if new else None
    change = SemesterChange(
        semester=semester,
        new_courses=new,
        old_courses=old,
        root=cfg.root,
        proposed_root=proposed,
        subfolders=subfolder_template(cfg),
        in_use={cfg.course_dir(c) for c in cfg.courses.values() if c.target},
    )
    change.targets = new_targets(change, new_root=proposed is not None)
    return change


def detect(client: Any, cfg: Config) -> SemesterChange | None:
    """``find_change`` with the data from Stud.IP (a ``StudipClient``)."""
    return find_change(
        cfg,
        client.courses(client.me()["id"]),
        client.semesters(),
        datetime.now().astimezone(),
        load_kept(),
    )


# -- proposals ------------------------------------------------------------------------

_COUNTED = re.compile(r"(.*?\bsemester[\s_-]*)(\d{1,2})", re.IGNORECASE)  # "Semester 5"
_ORDINAL = re.compile(r"(\d{1,2})(\.?[\s_-]*semester.*)", re.IGNORECASE)  # "5. Semester"
_NAMED = re.compile(
    r"\b(?:wise|sose|ws|ss|wintersemester|sommersemester)\b|\b20\d\d\b", re.IGNORECASE
)


def typed_root(cfg: Config, text: str) -> Path:
    """A folder typed by the user: "~/..." or absolute as given, anything else next
    to the current root ("Semester 6" -> sibling of "Semester 5"), never relative to
    the working directory, which differs for the background sync."""
    path = Path(text.strip()).expanduser()
    return path if path.is_absolute() else cfg.root.parent / path


def propose_root(root: Path, semester_title: str) -> Path | None:
    """A sibling folder for the new semester when the root is named after one.

    "Semester 5" -> "Semester 6", "5. Semester" -> "6. Semester",
    "WiSe 2026-27" -> "SoSe 2027" (the title from Stud.IP). Any other root name
    ("Uni", "Studium") is kept: the new course folders go next to the old ones.
    """
    name = root.name
    if m := _COUNTED.fullmatch(name):
        proposed = root.with_name(f"{m[1]}{int(m[2]) + 1}")
    elif m := _ORDINAL.fullmatch(name):
        proposed = root.with_name(f"{int(m[1]) + 1}{m[2]}")
    elif _NAMED.search(name):
        proposed = root.with_name(sanitize_filename(semester_title.replace("/", "-")))
    else:
        return None
    return None if proposed == root else proposed


def subfolder_template(cfg: Config) -> tuple[str, ...]:
    """Subfolders for the new course folders: the config's, else those the current
    course folders share (Folien, Übungen, ...), else the defaults."""
    if cfg.course_folders != DEFAULT_COURSE_FOLDERS:
        return cfg.course_folders
    dirs = [cfg.course_dir(c) for c in cfg.courses.values() if not c.exclude and c.target]
    return detect_course_folders(dirs) or cfg.course_folders


def new_targets(change: SemesterChange, *, new_root: bool) -> dict[str, str]:
    """A folder name per new course: an existing folder that fits, else the title."""
    root = change.target_root(new_root)
    # Never the folder of a set-up course: "Analysis II" must not land in "Analysis I".
    folders = [f for f in local_course_folders(root) if root / f not in change.in_use]
    # A course that repeats with the same title gets "Name (2)", not the old folder.
    taken = {d.name.casefold() for d in change.in_use if d.parent == root}
    targets: dict[str, str] = {}
    for course in change.new_courses:
        name = propose_target(course.title, folders) or course_folder_name(course.title)
        base, n = name, 2
        while name.casefold() in taken:
            name = f"{base} ({n})"
            n += 1
        taken.add(name.casefold())
        targets[course.id] = name
    return targets


# -- applying it ----------------------------------------------------------------------


@dataclass
class Choices:
    new_root: Path | None  # None: keep the root
    add: dict[str, str]  # new course id -> folder name below the (new) root
    skip: set[str]  # new courses not to sync
    archive: set[str]  # old course ids to stop syncing
    keep: set[str] = field(default_factory=set)  # ended courses to keep syncing
    read_only: bool = False


@dataclass
class Applied:
    config: Config
    created: list[Path] = field(default_factory=list)
    read_only_files: int = 0
    problems: list[str] = field(default_factory=list)


def default_choices(change: SemesterChange) -> Choices:
    return Choices(
        new_root=change.proposed_root,
        add=dict(change.targets),
        skip=set(),
        archive={o.config.id for o in change.old_courses if o.ended},
        keep={o.config.id for o in change.old_courses if not o.ended},
    )


def apply_change(cfg: Config, change: SemesterChange, choices: Choices) -> Applied:
    """Write the config once, then create the folders. Raises ConfigError/ValueError
    before anything is written; folder problems afterwards are reported, not raised."""
    home = Path.home()
    new_root = choices.new_root if choices.new_root and choices.new_root != cfg.root else None
    if new_root is not None and not new_root.is_absolute():
        raise ValueError(f"{new_root}: give a full path (e.g. ~/Uni/Semester 6)")
    if new_root is not None and (problem := unsafe_folder(str(new_root))):
        raise ValueError(f"{new_root}: {problem}")
    titles = {c.id: c.title for c in change.new_courses}
    for cid, target in choices.add.items():
        if cid not in titles:
            raise ValueError(f"{cid} is not a new course")
        if not target.strip() or "/" in target or target.strip() in (".", ".."):
            raise ValueError(f"{target!r}: give a folder name, not a path")

    courses: dict[str, dict[str, Any]] = {}
    if new_root is not None:
        # Set-up courses keep their folders: below the old root, now spelled out.
        for course in cfg.courses.values():
            folder = Path(course.target).expanduser() if course.target else None
            if folder is not None and not folder.is_absolute():
                courses[course.id] = {"target": home_path(cfg.root / folder, home)}
    for cid in choices.archive:
        courses.setdefault(cid, {})["exclude"] = True
    for cid, target in choices.add.items():
        courses[cid] = {"name": titles[cid], "target": target.strip(), "exclude": None}
    for cid in choices.skip - set(choices.add):
        if cid in titles:
            courses[cid] = {"name": titles[cid], "exclude": True}

    root_value = home_path(new_root, home) if new_root is not None else None
    config = edit_courses(cfg.path, root=root_value, courses=courses)  # validates first
    result = Applied(config)

    root = new_root or cfg.root
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.problems.append(f"{root}: {exc.strerror or exc}")
        return result
    for target in choices.add.values():
        folder = root / target.strip()
        if folder.is_dir():
            continue
        try:
            result.created.append(create_course_folder(root, target.strip(), change.subfolders))
        except OSError as exc:
            result.problems.append(f"{folder}: {exc.strerror or exc}")

    if choices.read_only:
        for cid in choices.archive:
            if cid in cfg.courses and cfg.courses[cid].target:
                result.read_only_files += make_read_only(cfg.course_dir(cfg.courses[cid]))
    ended_kept = {o.config.id for o in change.old_courses if o.ended} & choices.keep
    if ended_kept:
        save_kept(load_kept() | ended_kept)
    return result


def make_read_only(folder: Path) -> int:
    """Remove the write permission from every file below ``folder`` (not the folders,
    so you can still add notes). ``chmod -R u+w FOLDER`` undoes it."""
    count = 0
    if not folder.is_dir():
        return 0
    for directory, _, files in os.walk(folder):
        for name in files:
            path = Path(directory) / name
            with contextlib.suppress(OSError):
                if path.is_symlink():
                    continue
                mode = path.stat().st_mode
                if mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                    path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
                    count += 1
    return count


# -- ended courses the user keeps -------------------------------------------------------


def load_kept() -> set[str]:
    try:
        data = json.loads(paths.semester_kept_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def save_kept(ids: set[str]) -> None:
    path = paths.semester_kept_file()
    paths.private_dir(path.parent)
    path.write_text(json.dumps(sorted(ids)), encoding="utf-8")
    paths.private_file(path)
