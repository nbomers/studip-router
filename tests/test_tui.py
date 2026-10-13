from __future__ import annotations

from pathlib import Path

import pytest
import respx
from fake_studip import BASE, COURSE, OTHER, install
from textual.widgets import DataTable, Input, SelectionList, Static

from studip_router import paths
from studip_router.config import load
from studip_router.tui import ChoiceScreen, ConfirmScreen, HomeScreen, StudipRouterApp
from studip_router.tui.settings import SettingRow, SettingsScreen
from studip_router.tui.setup import SetupScreen
from studip_router.tui.widgets import HelpScreen


@pytest.fixture
def course_dir(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        "# keep me\n"
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return root / "Example Course"


async def wait_for(pilot, condition, tries: int = 60) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition not reached")


def config():
    return load(paths.config_file())


@respx.mock
async def test_home_shows_status_cards(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        assert isinstance(app.screen, HomeScreen)
        news = app.screen.query_one("#card-news", Static)
        await wait_for(pilot, lambda: "1" in str(news.render()))
        todo = str(app.screen.query_one("#card-todo", Static).render())
        assert "1 new course" in todo
        await pilot.press("question_mark")
        assert isinstance(app.screen, HelpScreen)


@respx.mock
async def test_sync_now_asks_once_then_downloads(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("s")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        assert "3 new" in app.screen.detail
        await pilot.press("y")
        await wait_for(pilot, lambda: (course_dir / "Sonstiges" / "Orga.pdf").exists())
        await wait_for(pilot, lambda: not app.screen.running)
        await pilot.press("escape", "w")
        assert app.screen.query_one(DataTable).row_count == 3


@respx.mock
async def test_preview_does_not_change_anything(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        table = app.screen.query_one(DataTable)
        await wait_for(pilot, lambda: table.row_count == 3)
        assert not isinstance(app.screen, ConfirmScreen)
        assert not (course_dir / "Sonstiges" / "Orga.pdf").exists()
        await pilot.press("s")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("n")
        assert not (course_dir / "Sonstiges" / "Orga.pdf").exists()


async def test_settings_change_values_and_keep_comments(course_dir: Path) -> None:
    app = StudipRouterApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("comma")
        assert isinstance(app.screen, SettingsScreen)
        rows = list(app.screen.query(SettingRow))
        assert rows[0].has_focus and rows[0].setting.label == "semester"
        await pilot.press("l")  # current -> all
        assert config().semester == "all"
        await pilot.press("j", "l")  # changed files: version -> overwrite
        assert config().on_update == "overwrite"
        await pilot.press("h", "h")  # wraps around: overwrite -> version -> skip
        assert config().on_update == "skip"
        theme_row = next(r for r in rows if r.setting.label == "theme")
        theme_row.focus()
        await pilot.press("l")
        assert app.theme == config().ui_theme != "tokyo-night"
        size_row = next(r for r in rows if r.setting.label == "size limit")
        size_row.focus()
        await pilot.press("h", "h", "h")  # 200 -> 100 -> 50 -> none
        assert config().exclude.max_size_mb == 0
        assert Path(paths.config_file()).read_text().startswith("# keep me")


async def test_auto_sync_setting_asks_first(course_dir: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr("studip_router.schedule.install", lambda m, p=None: calls.append(m))
    app = StudipRouterApp()
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("comma")
        row = next(r for r in app.screen.query(SettingRow) if r.setting.label == "auto-sync")
        row.focus()
        await pilot.press("l")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        assert calls == []
        await pilot.press("l", "y")
        assert calls == [30]


@respx.mock
async def test_courses_toggle_and_add(course_dir: Path, root: Path) -> None:
    install(respx.mock)
    (root / "Underwater Basket Weaving").mkdir()
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("c")
        table = app.screen.query_one(DataTable)
        await wait_for(pilot, lambda: table.row_count == 2)
        titles = [e.course.title for e in app.screen.entries]
        table.move_cursor(row=titles.index("Underwater Basket Weaving"))
        await pilot.press("space")
        assert isinstance(app.screen, ChoiceScreen)
        await pilot.press("enter")  # first choice = suggested folder
        assert config().courses[OTHER].target == "Underwater Basket Weaving"
        table.move_cursor(row=titles.index("Introduction to Quantum Cooking"))
        await pilot.press("space")
        assert config().courses[COURSE].exclude
        await pilot.press("space")
        assert not config().courses[COURSE].exclude


@respx.mock
async def test_rules_suggest_and_test(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("r")
        selection = app.screen.query_one(SelectionList)
        await wait_for(pilot, lambda: selection.option_count == 2)
        await pilot.press("w")
        await wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await wait_for(pilot, lambda: len(config().courses[COURSE].rules) == 2)
        app.screen.query_one("#test-course", Input).value = "quantum"
        app.screen.query_one("#test-folder", Input).value = "Übungsblätter"
        app.screen.query_one("#test-name", Input).value = "Blatt 3.pdf"
        await pilot.pause()
        result = str(app.screen.query_one("#test-result", Static).render())
        assert "Uebungen/Blatt 03.pdf" in result


@respx.mock
async def test_first_run_setup(root: Path, monkeypatch) -> None:
    install(respx.mock)
    stored = {}
    monkeypatch.setattr(
        "studip_router.auth.set_password", lambda url, user, pw: stored.update(pw=pw)
    )
    (root / "Quantum Cooking" / "Misc").mkdir(parents=True)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 50)) as pilot:
        assert isinstance(app.screen, SetupScreen)
        for field, value in (
            ("url", "studip.example.edu"),
            ("username", "jdoe"),
            ("password", "pw"),
            ("root", str(root)),
        ):
            app.screen.query_one(f"#{field}", Input).value = value
        await pilot.click("#connect")
        selection = app.screen.query_one(SelectionList)
        await wait_for(pilot, lambda: selection.option_count == 2)
        assert selection.selected == [0]  # only the course with a matching folder
        assert app.screen.query_one("#default-target", Input).value == "Misc"
        await pilot.click("#save")
        await wait_for(pilot, lambda: isinstance(app.screen, HomeScreen))
        cfg = config()
        assert cfg.courses[COURSE].target == "Quantum Cooking"
        assert cfg.courses[OTHER].exclude and cfg.default_target == "Misc"
        assert stored == {"pw": "pw"}
        assert "pw" not in Path(paths.config_file()).read_text()


async def test_invalid_config_shows_problem(course_dir: Path) -> None:
    Path(paths.config_file()).write_text("[server]\nbase_url = 'ftp://x'\n")
    app = StudipRouterApp()
    async with app.run_test() as pilot:
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("s")
        assert isinstance(app.screen, HomeScreen)  # nothing opens, a hint is shown
