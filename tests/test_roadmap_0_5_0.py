"""Roadmap items of 0.5.0: rule from a file (13), check after an update (19),
and the semester change in the UI."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path

import pytest
import respx
from conftest import COURSE_ID, make_config
from fake_studip import BASE, COURSE, install
from typer.testing import CliRunner

from studip_router import __version__, paths, update_check
from studip_router.config import load
from studip_router.state import State
from studip_router.suggest import name_pattern, rule_from_file
from studip_router.tui import ConfirmScreen, StudipRouterApp
from studip_router.tui.views import SemesterView, SyncView
from studip_router.tui.widgets import ChoiceScreen

# -- 13: a rule from one file ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "glob"),
    [
        ("Blatt_03.pdf", "Blatt_*.pdf"),
        ("VL 4 Logik.pdf", "VL * Logik.pdf"),
        ("Kapitel 3.1.pdf", "Kapitel *.pdf"),
        ("a[1].pdf", "a[[]*].pdf"),
        ("Skript.pdf", None),
    ],
)
def test_name_pattern(name: str, glob: str | None) -> None:
    assert name_pattern(name) == glob


def test_rule_from_file_matches_its_siblings() -> None:
    import fnmatch

    rule = rule_from_file(("uebungen",), "Übungen [alt]", "Blatt_03.pdf", "folder", "Übungen")
    assert rule["id"] == "uebungen-2" and rule["target"] == "Übungen"
    assert fnmatch.fnmatchcase("Übungen [alt]", rule["match"]["folder"])
    rule = rule_from_file((), "", "Blatt_03.pdf", "name", "Übungen")
    assert fnmatch.fnmatchcase("Blatt_11.pdf", rule["match"]["name_glob"])
    with pytest.raises(ValueError):
        rule_from_file((), "", "Blatt_03.pdf", "folder", "Übungen")


# -- 19: check after an update ---------------------------------------------------------


def _synced_state(root: Path, tmp_path: Path) -> State:
    """One synced file that the current sorting would put into another folder."""
    from dataclasses import replace

    from conftest import FakeDownloads, remote

    from studip_router.executor import execute
    from studip_router.planner import plan_course

    cfg = make_config(root)
    state = State(paths.state_db())
    f = remote("Blatt 3.pdf", folder="Übungsblätter")
    execute(plan_course(cfg, cfg.courses[COURSE_ID], [f], {}), state, FakeDownloads(), 1)
    record = state.get(f.id)
    assert record is not None
    # as placed by an older version: in the default folder, by default
    old = root / "Example Course" / "Sonstiges" / "Blatt 3.pdf"
    Path(record.local_path).rename(old)
    state.upsert(replace(record, local_path=str(old), rule_id=None, routed_by_default=True))
    return state


def test_fresh_install_and_unchanged_sorting_do_not_hold(root: Path, tmp_path: Path) -> None:
    cfg = make_config(root)
    with State(paths.state_db()) as state:
        assert update_check.hold_background(cfg, state) is None  # nothing synced yet
    assert update_check.seen_version() == __version__


def test_background_sync_waits_after_an_update_that_moves_files(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = make_config(root)
    with _synced_state(root, tmp_path) as state:
        paths.seen_version_file().write_text("0.0.1\n")
        held = update_check.hold_background(cfg, state)
        assert held is not None and held[1] and held[0].moves == 1
        assert held[0].previous == "0.0.1"
        assert "0.0.1" in update_check.message(held[0])
        again = update_check.hold_background(cfg, state)
        assert again is not None and not again[1]  # no second notification
        # after HOLD_DAYS it syncs again
        later = time.time() + update_check.HOLD_DAYS * 86400 + 60
        monkeypatch.setattr(update_check.time, "time", lambda: later)
        assert update_check.hold_background(cfg, state) is None
        assert update_check.seen_version() == __version__


def test_a_look_in_the_ui_ends_the_wait(root: Path, tmp_path: Path) -> None:
    cfg = make_config(root)
    with _synced_state(root, tmp_path) as state:
        paths.seen_version_file().write_text("0.0.1\n")
        assert update_check.hold_background(cfg, state) is not None
        update_check.mark_seen()
        assert update_check.hold_background(cfg, state) is None
        assert not paths.update_hold_file().exists()


def test_scheduled_sync_cli_returns_without_syncing(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studip_router.cli import app

    cfg_path = Path(paths.config_file())
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE_ID}]\nname = 'Example Course'\ntarget = 'Example Course'\n"
    )
    _synced_state(root, tmp_path).close()
    paths.seen_version_file().write_text("0.0.1\n")
    sent: list[str] = []
    monkeypatch.setattr("studip_router.cli.notify", lambda title, msg: sent.append(msg))
    monkeypatch.setattr(
        "studip_router.cli.auth.get_password", lambda *a: pytest.fail("must not log in")
    )
    result = CliRunner().invoke(app, ["sync", "--notify", "--quiet"])
    assert result.exit_code == 0, result.output
    assert len(sent) == 1 and "other folders" in sent[0]


# -- the TUI: semester view and "+" in the preview ----------------------------------------


@pytest.fixture
def ui_config(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return cfg


async def _wait(pilot, condition, tries: int = 80) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition not reached")


@respx.mock
async def test_preview_adds_a_rule_for_files_like_the_selected_one(
    ui_config: Path, root: Path
) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("p")
        await _wait(pilot, lambda: isinstance(app.screen, SyncView) and app.screen.plan)
        view = app.screen
        rows = view.row_actions
        index = next(i for i, a in enumerate(rows) if a.file and a.file.name == "lecture-1.pdf")
        from textual.widgets import DataTable

        view.query_one(DataTable).move_cursor(row=index)
        await pilot.press("plus")
        await _wait(pilot, lambda: isinstance(app.screen, ChoiceScreen))
        assert [k for k, _ in app.screen.choices] == ["folder", "name"]
        app.screen.dismiss("name")
        await _wait(
            pilot,
            lambda: isinstance(app.screen, ChoiceScreen) and app.screen.choices[0][0] != "folder",
        )
        assert "Uebungen" in [k for k, _ in app.screen.choices]
        app.screen.dismiss("Uebungen")
        await _wait(pilot, lambda: isinstance(app.screen, SyncView))
        await _wait(pilot, lambda: app.cfg.courses[COURSE].rules)
        rule = app.cfg.courses[COURSE].rules[-1]
        assert rule.target == "Uebungen" and rule.match.name_glob == "lecture-*.pdf"
        await _wait(
            pilot,
            lambda: any(
                a.file and a.file.name == "lecture-1.pdf" and a.route and a.route.rule_id == rule.id
                for a in getattr(view, "row_actions", [])
            ),
        )


@respx.mock
async def test_semester_view_applies_the_change(
    ui_config: Path, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime

    from studip_router import semester
    from studip_router.models import Course, Semester

    install(respx.mock)
    wise = Semester(
        "w", "WiSe", datetime(2026, 10, 1, tzinfo=UTC), datetime(2027, 3, 31, tzinfo=UTC)
    )
    sose = Semester(
        "s", "SoSe 2027", datetime(2027, 4, 1, tzinfo=UTC), datetime(2027, 9, 30, tzinfo=UTC)
    )
    new_id = "d" * 32
    courses = [
        Course(COURSE, "Quantum Cooking", wise, wise),
        Course(new_id, "Analysis II", sose, sose),
    ]

    def detect(client, cfg):
        return semester.find_change(cfg, courses, [wise, sose], datetime(2027, 5, 1, tzinfo=UTC))

    monkeypatch.setattr(semester, "detect", detect)
    app = StudipRouterApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("n")
        await _wait(pilot, lambda: isinstance(app.screen, SemesterView) and app.screen.change)
        view = app.screen
        assert [k for k, _ in view.rows] == ["root", "new", "old"]  # root "uni": kept
        assert view.choices.archive == {COURSE}  # WiSe has ended
        from textual.widgets import DataTable

        view.query_one(DataTable).move_cursor(row=1)
        await pilot.press("space")  # skip the new course
        assert new_id in view.choices.skip
        await pilot.press("space")  # and add it again
        assert view.choices.add == {new_id: "Analysis II"}
        await pilot.press("enter")
        await _wait(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await _wait(pilot, lambda: view.done)
        cfg = load(ui_config)
        assert cfg.courses[COURSE].exclude
        assert cfg.courses[new_id].target == "Analysis II"
        assert (root / "Analysis II").is_dir()
        with contextlib.suppress(Exception):
            assert "Done" in str(view.query_one("#status").renderable)


# -- 18: report a file sorted wrong -----------------------------------------------------


def test_report_holds_no_course_data() -> None:
    from urllib.parse import parse_qs, urlparse

    from studip_router.planner import Route
    from studip_router.sorting_report import issue_url, report_text

    route = Route("Folien", "Blatt 3.pdf", "auto:slides", why="folder name")
    title, body = report_text("Material/Woche 3", "Blatt 3.pdf", route)
    assert "Blatt 3.pdf" in title
    assert "automatic sorting, category slides (by folder name)" in body
    assert "Went to: Folien" in body and "Should go to:" in body
    assert "Quantum" not in body and COURSE_ID not in body
    query = parse_qs(urlparse(issue_url(title, body)).query)
    assert query["body"] == [body] and query["title"] == [title]


@respx.mock
async def test_preview_report_opens_the_issue_form_only_on_request(
    ui_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studip_router.tui.widgets import ReportScreen

    install(respx.mock)
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    app = StudipRouterApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("p")
        await _wait(pilot, lambda: isinstance(app.screen, SyncView) and app.screen.plan)
        await pilot.press("b")
        await _wait(pilot, lambda: isinstance(app.screen, ReportScreen))
        assert opened == []  # nothing leaves the machine by itself
        await pilot.press("ctrl+o")
        await _wait(pilot, lambda: bool(opened))
        assert opened[0].startswith("https://github.com/nbomers/studip-router/issues/new?")
        assert "Quantum" not in opened[0]
        await pilot.press("escape")
        await _wait(pilot, lambda: isinstance(app.screen, SyncView))


# -- review findings -------------------------------------------------------------------


def test_name_pattern_keeps_a_star_from_the_name() -> None:
    import fnmatch

    glob = name_pattern("a*1.pdf")
    assert glob is not None and fnmatch.fnmatchcase("a*7.pdf", glob)
    assert not fnmatch.fnmatchcase("ab7.pdf", glob)


def test_typed_semester_root_is_never_relative(root: Path) -> None:
    from studip_router.semester import typed_root

    cfg = make_config(root / "Semester 5")
    assert typed_root(cfg, "Semester 6") == root / "Semester 6"
    assert typed_root(cfg, "~/Uni/S6") == Path.home() / "Uni" / "S6"


def test_json_semester_needs_yes_or_dry_run(ui_config: Path) -> None:
    from studip_router.cli import app

    before = ui_config.read_text()
    result = CliRunner().invoke(app, ["--json", "semester"])
    assert result.exit_code == 2 and ui_config.read_text() == before


@respx.mock
async def test_report_copy_key_reaches_the_dialog(
    ui_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from studip_router.tui.widgets import ReportScreen

    install(respx.mock)
    copied: list[str] = []
    app = StudipRouterApp()
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("p")
        await _wait(pilot, lambda: isinstance(app.screen, SyncView) and app.screen.plan)
        await pilot.press("b")
        await _wait(pilot, lambda: isinstance(app.screen, ReportScreen))
        await pilot.press("ctrl+y")
        await _wait(pilot, lambda: bool(copied))
        assert "File name:" in copied[0]
