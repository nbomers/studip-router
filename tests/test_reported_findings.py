"""Regression tests for findings reported after 0.2.2 (each one reproduced first)."""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, remote
from fake_studip import API, BASE, COURSE, install
from textual.widgets import Input

from studip_router import paths
from studip_router.api import AuthFailed, StudipClient, _rel_id
from studip_router.executor import execute, fetch_verified
from studip_router.planner import Kind, plan_course
from studip_router.reorganize import pending_moves


# 1.1 suggested name rules match the umlaut spelling they were suggested for
def test_suggested_name_rules_match_umlauts(tmp_path: Path) -> None:
    from studip_router.rules import parse_rules
    from studip_router.suggest import suggest_rules

    (tmp_path / "Übungen").mkdir()
    (tmp_path / "Klausur").mkdir()
    files = [remote("Lösung_1.pdf"), remote("Übung_1.pdf"), remote("Prüfung_2025.pdf")]
    rules, problems = parse_rules([s.rule for s in suggest_rules(files, tmp_path)], "r")
    assert not problems
    for f in files:
        assert any(r.match.test(f)[0] for r in rules), f.name


# 1.2 a file only re-described or moved on Stud.IP keeps the rule that placed it
def test_refresh_keeps_the_placement_so_reorganize_sees_it(cfg, state, root: Path) -> None:
    (root / "Example Course" / "Uebungen").mkdir(exist_ok=True)
    first = replace(remote("blatt1.pdf"), content_id="c1")
    execute(plan_course(cfg, cfg.courses[COURSE_ID], [first], {}), state, FakeDownloads(), 1)
    moved = replace(first, folder_path="Übungen", chdate=datetime(2026, 10, 9, tzinfo=UTC))
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [moved], state.for_course(COURSE_ID))
    assert [a.kind for a in plan.actions] == [Kind.REFRESH]
    execute(plan, state, FakeDownloads(), 2)
    record = state.get(first.id)
    assert record.routed_by_default and record.rule_id is None  # still where the default put it
    assert [m.dst.parent.name for m in pending_moves(cfg, state)] == ["Uebungen"]


# 1.3 --include-large only lifts the size limit
def test_include_large_keeps_folder_exclusions(cfg) -> None:
    f = remote("img.png", folder="CoursewareImport 1")
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [f], {}, include_large=True)
    assert plan.actions[0].kind is Kind.SKIP


# 1.4 a rejected login is not swallowed per course
def test_build_plan_raises_a_rejected_login(cfg, state, monkeypatch) -> None:
    from studip_router import sync
    from studip_router.models import Course

    class Client:
        warnings: ClassVar[list[str]] = []

        def files(self, course_id: str) -> list:
            raise AuthFailed("authentication failed (HTTP 401)")

    entry = sync.CourseEntry(Course(COURSE_ID, "Q", None, None), cfg.courses[COURSE_ID])
    monkeypatch.setattr(sync, "resolve_semester", lambda client, wanted: None)
    monkeypatch.setattr(sync, "list_courses", lambda *a, **k: ([entry], []))
    with pytest.raises(AuthFailed):
        sync.build_plan(Client(), cfg, state)


@pytest.fixture
def setup(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Q'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "wrong")
    return root


@respx.mock
async def test_tui_pauses_and_asks_for_the_password_on_401(setup: Path) -> None:
    from studip_router.tui import StudipRouterApp
    from studip_router.tui.widgets import PasswordScreen

    install(respx.mock)
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(401))
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        for _ in range(60):
            await pilot.pause(0.05)
            if isinstance(app.screen, PasswordScreen):
                break
        assert isinstance(app.screen, PasswordScreen)
        assert paths.auth_pause_file().exists()


# 1.5 the UI writes the log too, and handlers are never doubled
def test_log_handler_is_added_once(tmp_path: Path) -> None:
    from studip_router.report import _PrivateLog, setup_file_logging

    root = logging.getLogger("studip_router")
    setup_file_logging(tmp_path / "a.log", False)
    setup_file_logging(tmp_path / "a.log", True)
    assert sum(isinstance(h, _PrivateLog) for h in root.handlers) == 1


@respx.mock
async def test_tui_writes_the_log(setup: Path) -> None:
    from studip_router.tui import StudipRouterApp

    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause()
        logging.getLogger("studip_router.test").info("from the ui")
    for handler in logging.getLogger("studip_router").handlers:
        handler.flush()
    assert "from the ui" in paths.log_file().read_text()


# 1.6 a re-upload is a new version only in the same folder
def test_same_name_in_another_folder_is_not_a_new_version(cfg, state, root: Path) -> None:
    old = remote("Lösung.pdf", id="old", folder="Übung 1", size=3)
    execute(plan_course(cfg, cfg.courses[COURSE_ID], [old], {}), state, FakeDownloads(), 1)
    other = remote("Lösung.pdf", id="new", folder="Klausur", size=4)
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [other], state.for_course(COURSE_ID))
    assert plan.actions[0].kind is Kind.DOWNLOAD
    same = remote("Lösung.pdf", id="new", folder="Übung 1", size=4)
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [same], state.for_course(COURSE_ID))
    assert plan.actions[0].kind is Kind.NEW_VERSION


# 1.7 course numbers: real roman numerals, only the trailing one counts
@pytest.mark.parametrize(
    ("title", "folders", "expected"),
    [
        ("X-Ray Physics I", ["X-Ray Physics"], "X-Ray Physics"),
        ("Seminar VII", ["Seminar 7"], "Seminar 7"),
        ("Analysis II", ["Analysis I"], None),
        ("Analysis II", ["Analysis"], "Analysis"),
        ("Mathe 2 für Informatiker", ["Mathe 2"], "Mathe 2"),
    ],
)
def test_course_numbers(title: str, folders: list[str], expected: str | None) -> None:
    from studip_router.wizard import propose_target

    assert propose_target(title, folders) == expected


# 2.2 the setup refuses a dangerous root before creating anything
@respx.mock
async def test_setup_refuses_the_home_folder_as_root(root: Path, monkeypatch) -> None:
    from studip_router.tui import StudipRouterApp

    install(respx.mock)
    home = root.parent
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    before = sorted(p.name for p in home.iterdir())
    app = StudipRouterApp()
    async with app.run_test(size=(120, 50)) as pilot:
        for field, value in (("url", "studip.example.edu"), ("username", "jdoe")):
            app.screen.query_one(f"#{field}", Input).value = value
        app.screen.query_one("#password", Input).value = "pw"
        await pilot.click("#connect")
        for _ in range(60):
            await pilot.pause(0.05)
            if app.screen.query_one("#step2").display:
                break
        app.screen.query_one("#root", Input).value = str(home)
        await pilot.click("#next")
        await pilot.pause(0.2)
        status = str(app.screen.query_one("#root-status").render())
        assert "home folder" in status
        assert not app.screen.query_one("#step3").display
    created = {p.name for p in home.iterdir()} - set(before)
    assert created <= {"state"}  # only the tool's own state folder (tests keep it here)


# 2.3 a transfer cut short twice at the same point is not a "wrong size"
def test_truncated_twice_is_still_an_error(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="incomplete download"):
        fetch_verified(
            remote("x.pdf", size=50 << 20),
            tmp_path / "x.pdf",
            lambda _f, sink: sink(b"y" * (16 << 20)),
        )
    assert not list(tmp_path.iterdir())


def test_a_slightly_wrong_size_with_equal_content_is_accepted(tmp_path: Path) -> None:
    _sha, written = fetch_verified(
        remote("x.pdf", size=1000), tmp_path / "x.pdf", lambda _f, sink: sink(b"y" * 990)
    )
    assert written


# 2.4 a file that turned out unchanged clears its failure count
def test_refresh_clears_the_failure_count(cfg, state, root: Path) -> None:
    f = remote("a.pdf")
    execute(plan_course(cfg, cfg.courses[COURSE_ID], [f], {}), state, FakeDownloads(), 1)
    state.record_failure(f.id, "earlier problem")
    changed = replace(f, chdate=datetime(2026, 10, 9, tzinfo=UTC))
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [changed], state.for_course(COURSE_ID))
    outcome = execute(plan, state, FakeDownloads(), 2)  # same bytes: nothing written
    assert outcome.done[0].kind is Kind.REFRESH
    assert f.id not in state.failures()


# 2.5 PHP writes empty objects as []
def test_lists_instead_of_objects_are_tolerated() -> None:
    assert _rel_id([], "parent") is None
    assert _rel_id({"parent": []}, "parent") is None


@respx.mock
def test_meta_page_as_list_does_not_crash() -> None:
    respx.get(f"{API}/semesters").mock(
        return_value=httpx.Response(200, json={"data": [], "meta": {"page": []}})
    )
    assert StudipClient(BASE, "u", "p").semesters() == []


# editor: quoted arguments, and a missing editor is a message, not a crash
def test_editor_with_quoted_path_and_missing_editor(tmp_path: Path, monkeypatch) -> None:
    from studip_router.config_edit import open_in_editor

    script = tmp_path / "my editor.sh"
    out = tmp_path / "opened.txt"
    script.write_text(f'#!/bin/sh\necho "$2" > "{out}"\n')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    target = tmp_path / "config.toml"
    monkeypatch.setenv("VISUAL", f"'{script}' --wait")
    assert open_in_editor(target) is None
    assert out.read_text().strip() == str(target)
    monkeypatch.setenv("VISUAL", "/does/not/exist")
    problem = open_in_editor(target)
    assert problem is not None and "could not start" in problem
    assert os.environ["VISUAL"] == "/does/not/exist"
