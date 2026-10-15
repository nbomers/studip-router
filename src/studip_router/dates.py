"""Course dates ("Termine"): report cancellations, moves and room changes, list exams.

Changes are found by comparing the course dates with the snapshot of the last
check. The first check of a course only takes the snapshot, so a new semester
does not start with a flood of messages.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .api import AuthFailed, StudipClient
from .models import CourseDate
from .state import State

log = logging.getLogger(__name__)


class ChangeKind(StrEnum):
    CANCELLED = "cancelled"
    RESTORED = "takes place again"
    MOVED = "moved"
    ROOM = "room changed"
    REMOVED = "removed"
    NEW_EXAM = "new exam date"


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    date: CourseDate  # the current date; for REMOVED the last known one
    detail: str = ""

    def text(self) -> str:
        line = f"{self.date.course}: {self.date.when()} {self.kind}"
        return f"{line} ({self.detail})" if self.detail else line


def _parse_dt(value: str | None) -> datetime | None:
    """Aware datetime; a time without offset is taken as local time."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
    if parsed is not None and parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def fetch_dates(client: StudipClient, course_id: str, course_title: str) -> list[CourseDate]:
    result = []
    for item, _ in client._paginate(f"/courses/{course_id}/events"):
        attrs = item.get("attributes") or {}
        start, end = _parse_dt(attrs.get("start")), _parse_dt(attrs.get("end"))
        if start is None:
            client.warnings.append(f"{course_title}: date {item.get('id')} without a start")
            continue
        result.append(
            CourseDate(
                id=item["id"],
                course_id=course_id,
                course=course_title,
                start=start,
                end=end or start,
                category=attrs.get("categories") or "",
                location=(attrs.get("location") or "").strip(),
                cancelled=bool(attrs.get("is-cancelled")),
                description=(attrs.get("description") or "").strip(),
            )
        )
    result.sort(key=lambda d: d.start)
    return result


def _span(d: CourseDate) -> str:
    return f"{d.when()}-{d.end.astimezone():%H:%M}"


def diff(old: dict[str, CourseDate], new: list[CourseDate], now: datetime) -> list[Change]:
    """Changes worth a message: only for dates that are still ahead."""
    changes: list[Change] = []
    current = {d.id: d for d in new}
    for d in new:
        before = old.get(d.id)
        if before is None:
            if d.is_exam and d.start >= now and not d.cancelled:
                changes.append(Change(ChangeKind.NEW_EXAM, d))
            continue
        if max(d.start, before.start) < now:
            continue  # history is not news
        if d.cancelled and not before.cancelled:
            changes.append(Change(ChangeKind.CANCELLED, d, d.description))
        elif before.cancelled and not d.cancelled:
            changes.append(Change(ChangeKind.RESTORED, d))
        if (d.start, d.end) != (before.start, before.end):
            changes.append(Change(ChangeKind.MOVED, d, f"was {_span(before)}"))
        if before.location and d.location != before.location:
            changes.append(
                Change(ChangeKind.ROOM, d, f"{before.location} → {d.location or 'no room'}")
            )
    for date_id, before in old.items():
        if date_id not in current and before.start >= now and not before.cancelled:
            changes.append(Change(ChangeKind.REMOVED, before))
    changes.sort(key=lambda c: c.date.start)
    return changes


def check_dates(
    client: StudipClient,
    state: State,
    courses: Iterable[tuple[str, str]],
    now: datetime | None = None,
) -> list[Change]:
    """Fetch the dates of ``courses`` (id, title), record and return what changed.

    A course that fails or suddenly has no dates at all keeps its old snapshot:
    an API hiccup must not turn into "every date was removed".
    """
    moment = now or datetime.now().astimezone()
    changes: list[Change] = []
    for course_id, title in courses:
        try:
            new = fetch_dates(client, course_id, title)
        except AuthFailed:
            raise  # a rejected login is not retried for every course
        except Exception as exc:  # dates are extra: never fail the sync because of them
            log.warning("dates of %s not checked: %s", title, exc, exc_info=True)
            client.warnings.append(f"{title}: dates not checked ({exc})")
            continue
        old = state.course_dates(course_id)
        if not old:
            state.replace_course_dates(course_id, new)  # first check: snapshot only
            continue
        if not new:
            continue
        # old snapshots carry the course title of back then; report the current one
        found = diff({d.id: replace(d, course=title) for d in old}, new, moment)
        state.replace_course_dates(course_id, new)
        state.add_date_changes(
            (c.date.course_id, c.date.id, str(c.kind), c.date.start, c.text()) for c in found
        )
        changes += found
    return changes


def upcoming_exams(state: State, now: datetime | None = None) -> list[CourseDate]:
    moment = now or datetime.now().astimezone()
    return [d for d in state.all_course_dates() if d.is_exam and d.end >= moment]
