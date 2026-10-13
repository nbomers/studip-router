"""Glue between API, config, state and planner: which courses, which semester."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from .api import ApiError, StudipClient
from .config import Config, CourseConfig
from .models import Course, Semester
from .planner import CoursePlan, build_router, plan_course
from .state import State


class CourseStatus(StrEnum):
    ACTIVE = "active"
    EXCLUDED = "excluded"
    UNCONFIGURED = "unconfigured"


@dataclass
class CourseEntry:
    course: Course
    config: CourseConfig | None

    @property
    def status(self) -> CourseStatus:
        if self.config is None:
            return CourseStatus.UNCONFIGURED
        return CourseStatus.EXCLUDED if self.config.exclude else CourseStatus.ACTIVE


@dataclass
class SyncPlan:
    semester: Semester | None
    plans: list[CoursePlan] = field(default_factory=list)
    unconfigured: list[Course] = field(default_factory=list)
    missing: list[CourseConfig] = field(default_factory=list)  # configured, not on Stud.IP


def _norm_semester(title: str) -> str:
    t = title.strip().lower()
    t = re.sub(r"^(ws|wise|wintersemester)\b\.?", "wise", t)
    t = re.sub(r"^(ss|sose|sommersemester)\b\.?", "sose", t)
    return re.sub(r"\s+", " ", t)


def resolve_semester(client: StudipClient, wanted: str) -> Semester | None:
    """ "current" -> current semester, "all" -> None, otherwise match by title."""
    if wanted == "all":
        return None
    if wanted == "current":
        return client.current_semester()
    key = _norm_semester(wanted)
    for sem in client.semesters():
        if _norm_semester(sem.title) == key:
            return sem
    titles = ", ".join(s.title for s in client.semesters()[-6:])
    raise ApiError(f"unknown semester {wanted!r} (recent: {titles})")


def list_courses(
    client: StudipClient, cfg: Config, semester: Semester | None
) -> tuple[list[CourseEntry], list[CourseConfig]]:
    user = client.me()
    courses = client.courses(user["id"])
    remote_ids = {c.id for c in courses}
    entries = [
        CourseEntry(c, cfg.courses.get(c.id))
        for c in courses
        if semester is None or c.runs_in(semester)
    ]
    entries.sort(key=lambda e: (e.status != CourseStatus.ACTIVE, e.course.title.casefold()))
    missing = [c for cid, c in cfg.courses.items() if cid not in remote_ids and not c.exclude]
    return entries, missing


def select_course(entries: list[CourseEntry], query: str) -> CourseEntry:
    q = query.casefold()
    exact = [e for e in entries if e.course.id == query]
    if exact:
        return exact[0]
    hits = [
        e
        for e in entries
        if e.config
        and not e.config.exclude
        and (
            q in e.course.title.casefold()
            or q in e.config.target.casefold()
            or e.course.id.startswith(query)
        )
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ApiError(f"no active course matches {query!r}")
    names = ", ".join(e.course.title for e in hits)
    raise ApiError(f"{query!r} is ambiguous: {names}")


def build_plan(
    client: StudipClient,
    cfg: Config,
    state: State,
    *,
    semester_name: str | None = None,
    course_query: str | None = None,
    include_large: bool = False,
) -> SyncPlan:
    semester = resolve_semester(client, semester_name or cfg.semester)
    entries, missing = list_courses(client, cfg, semester)
    result = SyncPlan(semester=semester, missing=missing)
    result.unconfigured = [e.course for e in entries if e.status is CourseStatus.UNCONFIGURED]

    selected = [e for e in entries if e.status is CourseStatus.ACTIVE]
    if course_query:
        selected = [select_course(entries, course_query)]

    for entry in selected:
        assert entry.config is not None
        files = client.files(entry.course.id)
        result.plans.append(
            plan_course(
                cfg,
                entry.config,
                files,
                state.for_course(entry.course.id),
                include_large=include_large,
                router=build_router(cfg, entry.config, entry.course.semester_title),
                fresh_since=semester.start if semester else None,
            )
        )
    return result
