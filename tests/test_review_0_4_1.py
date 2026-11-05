"""Regression tests for the findings of the 0.4.1 review (docs/changes-0.2.4-to-0.4.1.md, §16)."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, make_config, remote
from fake_studip import install
from textual.widgets import DataTable

from studip_router import notify as notify_mod
from studip_router import paths
from studip_router.api import StudipClient, _clean
from studip_router.config import parse
from studip_router.executor import execute
from studip_router.planner import Kind, plan_course
from studip_router.reorganize import plan_reorganize
from studip_router.report import esc
from studip_router.state import State
from studip_router.suggest import suggest_rules
from studip_router.sync import _implicit_target
from studip_router.tui import ConfirmScreen, StudipRouterApp
from studip_router.tui.views import HistoryView
from studip_router.tui.widgets import HelpScreen

# -- S1: terminal control characters ------------------------------------------


def test_json_strings_lose_terminal_escapes_but_keep_text() -> None:
    doc = {"data": [{"title": "A\x1b]8;;https://evil\x1b\\click\x1b[2J", "t": "a\nb\tc"}]}
    clean = _clean(doc)
    assert clean["data"][0]["title"] == "A]8;;https://evil\\click[2J"
    assert clean["data"][0]["t"] == "a\nb\tc"
    assert _clean({"name": "Icon\r"}) == {"name": "Icon\r"}  # the system file stays recognisable


def test_esc_drops_control_characters() -> None:
    assert esc("x\x1b[31my\x9b") == "x[31my"


# -- S7: Retry-After ------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"), [("-1", 0.0), ("nan", None), ("5", 5.0), ("999", 60.0), ("x", None)]
)
def test_retry_after_is_clamped(header: str, expected: float | None) -> None:
    response = httpx.Response(503, headers={"Retry-After": header})
    assert StudipClient._retry_after(response) == expected


# -- S2: implicit course folders --------------------------------------------------


def test_course_title_tilde_is_not_the_home_folder() -> None:
    assert _implicit_target("~") == "_"
    assert _implicit_target("~jdoe") == "_jdoe"
    assert _implicit_target("Mathe") == "Mathe"


# -- S3: no silent overwrite -------------------------------------------------------


def test_file_saved_during_download_is_never_replaced(state, root: Path) -> None:
    cfg = make_config(root)
    f = remote("Orga.pdf")
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [f], {})
    dest = next(a.dest for a in plan.actions if a.kind is Kind.DOWNLOAD)

    def download(file_id: str, sink) -> None:
        dest.write_bytes(b"mine")  # the user saves a file of the same name meanwhile
        sink(b"abc")

    outcome = execute(plan, state, download, run_id=1)
    assert dest.read_bytes() == b"mine"
    assert len(outcome.failed) == 1
    assert not list(dest.parent.glob("*.part*"))


# -- S4: notify-send ---------------------------------------------------------------


def test_notify_send_gets_no_options_or_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(notify_mod.sys, "platform", "linux")
    monkeypatch.setattr(notify_mod.shutil, "which", lambda _: "/usr/bin/notify-send")
    monkeypatch.setattr(notify_mod.subprocess, "run", lambda args, **_: calls.append(args))
    notify_mod.notify("Stud.IP", "--urgency=critical <a href='x'>A & B</a>")
    assert calls[0][:2] == ["notify-send", "--"]
    assert calls[0][3] == "--urgency=critical &lt;a href='x'&gt;A &amp; B&lt;/a&gt;"


# -- S5: profile from the environment ------------------------------------------------


def test_profile_from_environment_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    paths.set_profile(None)
    monkeypatch.setenv("STUDIP_ROUTER_PROFILE", "../x")
    with pytest.raises(ValueError):
        paths.profile()
    monkeypatch.setenv("STUDIP_ROUTER_PROFILE", "uni-b")
    assert paths.profile() == "uni-b"


# -- B1, B2: progress, Esc and archives ----------------------------------------------


def _zip(**members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def test_stop_right_after_an_archive_still_extracts_it(state, root: Path) -> None:
    cfg = make_config(root, unzip=True)
    data = _zip(**{"a.txt": "a"})
    f = replace(remote("Paket.zip", size=len(data), mime="application/zip"), content_id="c1")
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [f], {})

    def stop(_action) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute(plan, state, FakeDownloads({f.id: data}), run_id=1, on_progress=stop)
    assert (root / "Example Course" / "Sonstiges" / "Paket" / "a.txt").read_text() == "a"


def test_failed_files_report_progress_and_can_stop(state, root: Path) -> None:
    cfg = make_config(root)
    files = [remote("a.pdf"), remote("b.pdf"), remote("c.pdf")]
    plan = plan_course(cfg, cfg.courses[COURSE_ID], files, {})
    seen: list[str] = []

    def broken(file_id: str, sink) -> None:
        raise OSError("network down")

    def failed(action, message: str) -> None:
        seen.append(action.file.name)
        if len(seen) == 2:
            raise KeyboardInterrupt  # Esc while everything fails

    with pytest.raises(KeyboardInterrupt):
        execute(plan, state, broken, run_id=1, on_failed=failed)
    assert seen == ["a.pdf", "b.pdf"]


# -- B4: reorganize knows the folder type --------------------------------------------


def test_reorganize_keeps_files_placed_by_folder_type(state, root: Path) -> None:
    cfg = make_config(root)
    f = replace(remote("Meier.pdf", folder="Gruppe A"), folder_type="HomeworkFolder")
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [f], {})
    execute(plan, state, FakeDownloads(), run_id=1)
    assert (root / "Example Course" / "Uebungen" / "Meier.pdf").exists()
    assert state.get(f.id).folder_type == "HomeworkFolder"
    assert plan_reorganize(cfg, state) == []

    # synced by 0.4.1 (no folder type stored): never sent to the default folder
    record = state.get(f.id)
    state.upsert(replace(record, folder_type=""))
    assert plan_reorganize(cfg, state) == []


def test_reorganize_keeps_names_that_need_the_semester(state, root: Path) -> None:
    rules = [
        {
            "id": "sheets",
            "match": {"name_regex": "Blatt"},
            "target": "Uebungen",
            "rename": "{semester}_{orig_name}",
        }
    ]
    cfg = parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root)},
            "courses": {COURSE_ID: {"target": "Example Course", "rules": rules}},
        },
        Path("c.toml"),
    )
    from studip_router.planner import build_router

    course = cfg.courses[COURSE_ID]
    f = remote("Blatt 1.pdf")
    plan = plan_course(cfg, course, [f], {}, router=build_router(cfg, course, "WiSe 2026-27"))
    execute(plan, state, FakeDownloads(), run_id=1)
    names = [p.name for p in (root / "Example Course" / "Uebungen").iterdir()]
    assert len(names) == 1 and names[0].startswith("WiSe 2026-27_")
    assert plan_reorganize(cfg, state) == []  # no rename to "_Blatt 1.pdf"


# -- B6, B7: rule suggestions ----------------------------------------------------------


def _course(root: Path) -> Path:
    course = root / "Example Course"
    for sub in ("Loesungen", "Folien", "Klausuren"):
        (course / sub).mkdir(exist_ok=True)
    return course


def test_no_name_rules_while_automatic_sorting_does_it(root: Path) -> None:
    files = [remote("Lösung Blatt 3.pdf"), remote("Uebungsblatt 4.pdf")]
    assert suggest_rules(files, _course(root), by_name=False) == []


def test_name_rules_follow_the_classifier_order_and_are_not_repeated(root: Path) -> None:
    files = [remote("Lösung Blatt 3.pdf"), remote("Uebungsblatt 4.pdf"), remote("Klausur.pdf")]
    first = suggest_rules(files, _course(root))
    assert [s.category for s in first] == ["exams", "solutions", "exercises"]
    again = suggest_rules(files, _course(root), {s.rule["id"] for s in first})
    assert again == []


# -- U1: a finished plan is never run twice ------------------------------------------------


@pytest.fixture
def course_dir(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from fake_studip import BASE, COURSE

    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return root / "Example Course"


async def _wait(pilot, condition, tries: int = 80) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition not reached")


@respx.mock
async def test_enter_after_a_sync_does_not_run_the_old_plan(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("s")
        await _wait(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await _wait(pilot, lambda: (course_dir / "Sonstiges" / "Orga.pdf").exists())
        view = app.screen
        await _wait(pilot, lambda: not view.running)
        await pilot.press("enter")  # reloads instead of re-running the finished plan
        await _wait(pilot, lambda: view.plan is not None and not view.used)
        assert not isinstance(app.screen, ConfirmScreen)
        assert view.query_one(DataTable).display
        with State(paths.state_db()) as state:
            assert state.pending_failures() == 0


@respx.mock
async def test_question_mark_opens_help_on_every_view(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("w")
        await _wait(pilot, lambda: isinstance(app.screen, HistoryView))
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)
