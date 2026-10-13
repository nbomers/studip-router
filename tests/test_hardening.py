"""Regression tests for problems found by comparing with similar tools."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, make_config, remote
from fake_studip import API, BASE, COURSE, OTHER, install

from studip_router import paths
from studip_router.api import StudipClient
from studip_router.config import parse
from studip_router.executor import execute
from studip_router.fsutil import Locked, exclusive_lock
from studip_router.models import Course
from studip_router.planner import Kind, SkipReason, plan_course, same_time
from studip_router.state import State
from studip_router.sync import build_plan, execute_plan

D1 = datetime(2026, 10, 1, 12, tzinfo=UTC)
D2 = datetime(2026, 11, 1, 12, tzinfo=UTC)


def _plan(cfg, state, files, **kw):
    return plan_course(cfg, cfg.courses[COURSE_ID], files, state.for_course(COURSE_ID), **kw)


def _sync(cfg, state, files, downloads=None, **kw):
    plan = _plan(cfg, state, files, **kw)
    execute(plan, state, downloads or FakeDownloads(), run_id=1)
    return plan


def _names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# -- one broken course must not stop the others (studip-sync #26) ---------------------


@respx.mock
def test_broken_course_does_not_abort_sync(root: Path, tmp_path: Path) -> None:
    install(respx.mock)
    respx.get(f"{API}/courses/{OTHER}/folders").mock(return_value=httpx.Response(500))
    (root / "Other").mkdir()
    cfg = parse(
        {
            "server": {"base_url": BASE, "username": "u"},
            "sync": {"root": str(root)},
            "courses": {
                COURSE: {"name": "Good", "target": "Example Course"},
                OTHER: {"name": "Broken", "target": "Other"},
            },
        },
        Path("c"),
    )
    with StudipClient(BASE, "u", "x") as client, State(tmp_path / "s.sqlite") as state:
        plan = build_plan(client, cfg, state)
    assert [p.course.id for p in plan.plans] == [COURSE]
    assert plan.errors and plan.errors[0][0] == "Broken"


@respx.mock
def test_unexpected_api_record_is_skipped_not_fatal() -> None:
    respx.get(f"{API}/courses/c/folders").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"page": {"total": 0}}})
    )
    respx.get(f"{API}/courses/c/file-refs").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "bad", "attributes": {"name": "x.pdf"}},  # no date at all
                    {
                        "id": "ok",
                        "attributes": {
                            "name": "y.pdf",
                            "chdate": "2026-10-01T10:00:00Z",
                            "filesize": 1,
                        },
                    },
                ],
                "meta": {"page": {"total": 2}},
            },
        )
    )
    client = StudipClient(BASE, "u", "x")
    files = client.files("c")
    assert [f.id for f in files] == ["ok"]
    assert files[0].chdate.tzinfo is not None
    assert "bad" in client.warnings[0]


# -- changes on Stud.IP that are not new content (Moodle-DL #206) ---------------------


def test_rename_on_studip_follows_locally_without_v2(cfg, state, root: Path) -> None:
    folder = root / "Example Course" / "Sonstiges"
    _sync(cfg, state, [replace(remote("a.pdf", id="f", chdate=D1), content_id="blob1")])
    renamed = replace(remote("Kapitel 1.pdf", id="f", chdate=D2), content_id="blob1")
    downloads = FakeDownloads()
    plan = _sync(cfg, state, [renamed], downloads)
    assert [a.kind for a in plan.actions] == [Kind.MOVE]
    assert downloads.calls == []
    assert _names(folder) == ["Kapitel 01.pdf"]
    assert _plan(cfg, state, [renamed]).actions == []


def test_metadata_change_is_recorded_without_download(cfg, state, root: Path) -> None:
    _sync(cfg, state, [replace(remote("a.pdf", id="f", chdate=D1), content_id="blob1")])
    moved = replace(remote("a.pdf", id="f", chdate=D2, folder="Neu"), content_id="blob1")
    downloads = FakeDownloads()
    plan = _sync(cfg, state, [moved], downloads)
    assert [a.kind for a in plan.actions] == [Kind.REFRESH] and downloads.calls == []
    assert _plan(cfg, state, [moved]).actions == []


def test_identical_content_never_becomes_v2(cfg, state, root: Path) -> None:
    """No content id (older Stud.IP): the hash decides after the download."""
    folder = root / "Example Course" / "Sonstiges"
    _sync(cfg, state, [remote("a.pdf", id="f", chdate=D1)], FakeDownloads({"f": b"abc"}))
    plan = _sync(cfg, state, [remote("a.pdf", id="f", chdate=D2)], FakeDownloads({"f": b"abc"}))
    assert plan.actions[0].kind is Kind.REFRESH
    assert _names(folder) == ["a.pdf"]
    assert _plan(cfg, state, [remote("a.pdf", id="f", chdate=D2)]).actions == []


def test_reupload_with_new_id_becomes_new_version(cfg, state, root: Path) -> None:
    folder = root / "Example Course" / "Sonstiges"
    _sync(cfg, state, [remote("Blatt.pdf", id="old")], FakeDownloads({"old": b"abc"}))
    plan = _sync(
        cfg,
        state,
        [remote("Blatt.pdf", id="new", size=4, chdate=D2)],
        FakeDownloads({"new": b"abcd"}),
    )
    assert [a.kind for a in plan.actions] == [Kind.NEW_VERSION]
    assert plan.remote_deleted == []
    assert _names(folder) == ["Blatt.pdf", "Blatt_v2.pdf"]
    assert set(state.for_course(COURSE_ID)) == {"new"}


def test_identical_reupload_is_just_relinked(cfg, state, root: Path) -> None:
    folder = root / "Example Course" / "Sonstiges"
    _sync(cfg, state, [remote("Blatt.pdf", id="old")], FakeDownloads({"old": b"abc"}))
    _sync(cfg, state, [remote("Blatt.pdf", id="new", chdate=D2)], FakeDownloads({"new": b"abc"}))
    assert _names(folder) == ["Blatt.pdf"]
    assert set(state.for_course(COURSE_ID)) == {"new"}


def test_date_format_change_is_not_a_change(cfg, state) -> None:
    utc = datetime(2026, 10, 1, 10, tzinfo=UTC)
    _sync(cfg, state, [remote("a.pdf", chdate=utc)])
    same_instant_cest = utc.astimezone(timezone(timedelta(hours=2)))  # 12:00+02:00
    assert same_time("2026-10-01T10:00:00Z", utc)
    assert _plan(cfg, state, [remote("a.pdf", chdate=same_instant_cest)]).actions == []


def test_remote_deletion_is_reported_once(root: Path, tmp_path: Path) -> None:
    cfg = make_config(root)
    with State(tmp_path / "s.sqlite") as state:
        _sync(cfg, state, [remote("a.pdf")])
        first = _plan(cfg, state, [])
        assert len(first.remote_deleted) == 1
        state.mark_gone([r.file_id for r in first.remote_deleted])
        assert _plan(cfg, state, []).remote_deleted == []
        assert (root / "Example Course" / "Sonstiges" / "a.pdf").exists()


# -- state loss, ignore list, failures ------------------------------------------------


def test_versions_are_recognised_after_state_loss(cfg, tmp_path: Path, root: Path) -> None:
    folder = root / "Example Course" / "Sonstiges"
    (folder / "a.pdf").write_bytes(b"old")
    (folder / "a_v2.pdf").write_bytes(b"newer")
    with State(tmp_path / "fresh.sqlite") as state:
        plan = _plan(cfg, state, [remote("a.pdf", size=5)])
    assert [(a.kind, a.dest.name) for a in plan.actions] == [(Kind.ADOPT, "a_v2.pdf")]


def test_ignored_file_is_skipped(cfg, state) -> None:
    state.ignore("file-a.pdf", COURSE_ID, "a.pdf")
    plan = _plan(cfg, state, [remote("a.pdf")], ignored=state.ignored_ids())
    assert plan.actions[0].reason == SkipReason.IGNORED


def test_repeated_failures_back_off(cfg, state, root: Path) -> None:
    def broken(_id, _sink):
        raise httpx.ReadError("nope")

    for _ in range(3):
        _sync(cfg, state, [remote("a.pdf")], broken, failures=state.failures())
    plan = _plan(cfg, state, [remote("a.pdf")], failures=state.failures())
    assert plan.actions[0].kind is Kind.SKIP and "failed 3x" in plan.actions[0].reason
    # after a success the counter is cleared
    _sync(cfg, state, [remote("a.pdf")], FakeDownloads())
    assert state.failures() == {}


# -- semesters, implicit courses, missing course folder --------------------------------


def test_course_without_semester_is_always_current() -> None:
    from conftest import remote as _  # noqa: F401

    from studip_router.models import Semester

    sem = Semester("s", "WiSe", D1, D2)
    assert Course("x", "Study group", None, None).runs_in(sem)


@respx.mock
def test_configured_course_keeps_syncing_after_semester_change(root: Path, tmp_path: Path):
    install(respx.mock)
    old = "01d01d01d01d01d01d01d01d01d01d01"  # "Old Course" runs in the previous semester
    respx.get(f"{API}/courses/{old}/folders").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"page": {"total": 0}}})
    )
    respx.get(f"{API}/courses/{old}/file-refs").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"page": {"total": 0}}})
    )
    (root / "Old").mkdir()
    cfg = parse(
        {
            "server": {"base_url": BASE, "username": "u"},
            "sync": {"root": str(root)},
            "courses": {COURSE: {"target": "Example Course"}, old: {"target": "Old"}},
        },
        Path("c"),
    )
    with StudipClient(BASE, "u", "x") as client, State(tmp_path / "s.sqlite") as state:
        plan = build_plan(client, cfg, state)
        strict = build_plan(client, cfg, state, semester_name="WiSe 2026/27")
    assert {p.course.id for p in plan.plans} == {COURSE, old}
    assert {p.course.id for p in strict.plans} == {COURSE}


@respx.mock
def test_only_configured_courses_false_syncs_everything(root: Path, tmp_path: Path) -> None:
    install(respx.mock)
    respx.get(f"{API}/courses/{OTHER}/folders").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"page": {"total": 0}}})
    )
    respx.get(f"{API}/courses/{OTHER}/file-refs").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"page": {"total": 0}}})
    )
    cfg = parse(
        {
            "server": {"base_url": BASE, "username": "u"},
            "sync": {"root": str(root), "only_configured_courses": False},
            "courses": {COURSE: {"target": "Example Course"}},
        },
        Path("c"),
    )
    with StudipClient(BASE, "u", "x") as client, State(tmp_path / "s.sqlite") as state:
        plan = build_plan(client, cfg, state)
    implicit = next(p for p in plan.plans if p.course.id == OTHER)
    assert implicit.course_dir == root / "Underwater Basket Weaving"
    assert implicit.actions[0].kind is Kind.MKDIR
    assert plan.unconfigured == []


def test_missing_course_folder_is_one_clear_error(root: Path, state) -> None:
    cfg = parse(
        {
            "server": {"base_url": BASE, "username": "u"},
            "sync": {"root": str(root), "create_missing_dirs": True},
            "courses": {COURSE_ID: {"target": "Renamed Folder"}},
        },
        Path("c"),
    )
    plan = _plan(cfg, state, [remote("a.pdf"), remote("b.pdf")])
    assert [a.kind for a in plan.actions] == [Kind.ERROR]
    assert "course folder does not exist" in plan.actions[0].reason
    assert not (root / "Renamed Folder").exists()


# -- concurrency ------------------------------------------------------------------------


def test_second_run_is_refused_while_one_is_active(cfg, state) -> None:
    with exclusive_lock(paths.lock_file()), pytest.raises(Locked):  # noqa: SIM117
        with exclusive_lock(paths.lock_file()):
            pass


def test_temp_files_are_unique_and_stale_ones_cleaned(tmp_path: Path) -> None:
    import os
    import time

    from studip_router.fsutil import cleanup_stale_parts, part_path

    dest = tmp_path / "a.pdf"
    assert part_path(dest) != part_path(dest)
    stale = part_path(dest)
    stale.write_bytes(b"x")
    os.utime(stale, (time.time() - 3 * 86400,) * 2)
    fresh = part_path(dest)
    fresh.write_bytes(b"x")
    mine = tmp_path / "notes.part"  # not ours: never touched
    mine.write_bytes(b"x")
    os.utime(mine, (time.time() - 3 * 86400,) * 2)
    assert cleanup_stale_parts(tmp_path) == [stale]
    assert fresh.exists() and mine.exists()


def test_parallel_writers_do_not_share_temp_files(cfg, root: Path, tmp_path: Path) -> None:
    from studip_router.executor import fetch_verified

    dest = root / "Example Course" / "Sonstiges" / "big.pdf"
    file = remote("big.pdf", size=50_000)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def slow(_id, sink):
        barrier.wait()
        for _ in range(50):
            sink(b"x" * 1000)

    def run():
        try:
            fetch_verified(file, dest, slow)
        except Exception as exc:  # pragma: no cover - would be the bug
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and dest.stat().st_size == 50_000


def test_execute_plan_holds_the_lock(cfg, state, monkeypatch) -> None:
    from studip_router.sync import SyncPlan

    with exclusive_lock(paths.lock_file()), pytest.raises(Locked):
        execute_plan(SyncPlan(semester=None), cfg, state, client=None)  # type: ignore[arg-type]


def test_restore_uses_original_name_when_all_versions_are_gone(cfg, state, root) -> None:
    folder = root / "Example Course" / "Sonstiges"
    _sync(cfg, state, [remote("a.pdf", chdate=D1)], FakeDownloads({"file-a.pdf": b"abc"}))
    _sync(cfg, state, [remote("a.pdf", size=4, chdate=D2)], FakeDownloads({"file-a.pdf": b"abcd"}))
    assert _names(folder) == ["a.pdf", "a_v2.pdf"]
    for f in folder.iterdir():
        f.unlink()
    plan = _sync(
        cfg, state, [remote("a.pdf", size=4, chdate=D2)], FakeDownloads({"file-a.pdf": b"abcd"})
    )
    assert plan.actions[0].dest.name == "a.pdf" and _names(folder) == ["a.pdf"]
    assert _plan(cfg, state, [remote("a.pdf", size=4, chdate=D2)]).actions == []
