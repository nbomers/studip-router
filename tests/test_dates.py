"""Course dates: cancellations, moves, room changes and exams."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import respx
from conftest import COURSE_ID
from fake_studip import API, BASE, COURSE, EVENTS, course_event, install, install_events
from test_cli import run, setup  # noqa: F401  (fixture)

from studip_router.api import StudipClient
from studip_router.dates import ChangeKind, check_dates, diff, upcoming_exams
from studip_router.models import CourseDate
from studip_router.notify import dates_message
from studip_router.state import State

NOW = datetime(2030, 11, 1, 12, tzinfo=UTC)


def date(id: str = "d1", days: float = 3, **kw) -> CourseDate:
    start = NOW + timedelta(days=days)
    fields = {
        "id": id,
        "course_id": COURSE_ID,
        "course": "Quantum Cooking",
        "start": start,
        "end": start + timedelta(hours=2),
        "category": "Präsenz-Sitzung",
        "location": "Room 1",
        "cancelled": False,
    }
    return CourseDate(**{**fields, **kw})


def kinds(old: list[CourseDate], new: list[CourseDate]) -> list[ChangeKind]:
    return [c.kind for c in diff({d.id: d for d in old}, new, NOW)]


# -- what counts as a change ------------------------------------------------------------


def test_nothing_changed() -> None:
    assert kinds([date()], [date()]) == []


def test_cancelled_and_restored() -> None:
    assert kinds([date()], [date(cancelled=True)]) == [ChangeKind.CANCELLED]
    assert kinds([date(cancelled=True)], [date()]) == [ChangeKind.RESTORED]


def test_moved_reports_the_old_time() -> None:
    old, new = date(), date(days=4)
    [change] = diff({old.id: old}, [new], NOW)
    assert change.kind is ChangeKind.MOVED
    assert change.detail.startswith("was ")


def test_room_change_but_not_a_first_room() -> None:
    [change] = diff({"d1": date()}, [date(location="Aula")], NOW)
    assert change.kind is ChangeKind.ROOM and change.detail == "Room 1 → Aula"
    # rooms are often filled in late: that is no news
    assert kinds([date(location="")], [date(location="Aula")]) == []


def test_removed_future_date() -> None:
    assert kinds([date(), date("d2")], [date()]) == [ChangeKind.REMOVED]


def test_past_dates_are_not_news() -> None:
    past = date(days=-2)
    assert kinds([past, date("d2", days=-1)], [replace(past, cancelled=True)]) == []


def test_only_new_exams_are_reported_as_new() -> None:
    new = [date(), date("d2"), date("exam", days=60, category="Klausur")]
    assert kinds([date()], new) == [ChangeKind.NEW_EXAM]


def test_exam_categories() -> None:
    assert date(category="Klausur").is_exam
    assert date(category="Nachprüfung").is_exam
    assert not date(category="Vorlesung").is_exam


# -- checking against Stud.IP -----------------------------------------------------------


def _check(state: State, events: list[dict] | None) -> list:
    with respx.mock:
        install_events(respx.mock, events)
        with StudipClient(BASE, "jdoe", "pw") as client:
            return check_dates(client, state, [(COURSE, "Quantum Cooking")], NOW)


def test_first_check_only_takes_a_snapshot(state: State) -> None:
    assert _check(state, None) == []
    assert len(state.course_dates(COURSE)) == len(EVENTS)


def test_second_check_finds_and_records_changes(state: State) -> None:
    _check(state, None)
    changed = [
        course_event(
            "ev-1", "2030-11-04T10:00:00+01:00", end="2030-11-04T12:00:00+01:00", cancelled=True
        ),
        EVENTS[1],
    ]
    [change] = _check(state, changed)
    assert change.kind is ChangeKind.CANCELLED
    assert "Quantum Cooking" in change.text()
    assert [
        r["kind"] for r in state.date_changes_since(datetime.now(UTC) - timedelta(minutes=1))
    ] == ["cancelled"]
    assert _check(state, changed) == []  # reported once


def test_empty_answer_keeps_the_snapshot(state: State) -> None:
    _check(state, None)
    assert _check(state, []) == []  # an API hiccup is not "every date removed"
    assert len(state.course_dates(COURSE)) == len(EVENTS)


def test_broken_answer_never_fails_the_sync(state: State) -> None:
    with respx.mock:
        respx.get(f"{API}/courses/{COURSE}/events").mock(
            return_value=httpx.Response(200, json={"data": [{"id": "x", "attributes": None}]})
        )
        with StudipClient(BASE, "jdoe", "pw") as client:
            assert check_dates(client, state, [(COURSE, "Quantum Cooking")], NOW) == []
            assert client.warnings  # reported, not raised


def test_upcoming_exams(state: State) -> None:
    _check(state, None)
    assert [d.id for d in upcoming_exams(state, NOW)] == ["ev-exam"]
    assert upcoming_exams(state, NOW + timedelta(days=400)) == []


def test_notification_lists_a_few_changes() -> None:
    assert dates_message([]) is None
    message = dates_message([f"change {i}" for i in range(5)])
    assert message is not None
    assert message.splitlines() == [
        "change 0",
        "change 1",
        "change 2",
        "and 2 more - `studip dates`",
    ]


# -- command line -----------------------------------------------------------------------


@respx.mock
def test_sync_reports_changed_dates(setup: Path) -> None:  # noqa: F811
    install(respx.mock)
    first = json.loads(run("--json", "sync").output)
    assert first["date_changes"] == []

    install_events(respx.mock, [replace_location(EVENTS[0], "Room 2"), EVENTS[1]])
    again = json.loads(run("--json", "sync").output)
    assert [(c["kind"], c["detail"]) for c in again["date_changes"]] == [
        ("room changed", "Room 1 → Room 2")
    ]


@respx.mock
def test_dates_command_lists_exams(setup: Path) -> None:  # noqa: F811
    install(respx.mock)
    data = json.loads(run("--json", "dates").output)
    assert data["changes"] == []
    assert [(e["course"], e["location"]) for e in data["exams"]] == [("Quantum Cooking", "Aula")]
    offline = run("dates", "--offline")
    assert offline.exit_code == 0 and "Aula" in offline.output


def test_watching_dates_can_be_turned_off(setup: Path) -> None:  # noqa: F811
    from studip_router.config import load
    from studip_router.paths import config_file

    path = config_file()
    path.write_text(path.read_text().replace("[sync]\n", "[sync]\nwatch_dates = false\n"))
    assert load(path).watch_dates is False


def replace_location(event: dict, location: str) -> dict:
    return {**event, "attributes": {**event["attributes"], "location": location}}
