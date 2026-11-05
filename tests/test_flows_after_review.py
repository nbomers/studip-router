"""End-to-end flows after the 0.4.1 review: CLI and TUI against the fake Stud.IP."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx
from fake_studip import API, BASE, COURSE, install
from typer.testing import CliRunner

from studip_router import paths
from studip_router.cli import app

runner = CliRunner()


def run(*args: str):
    return runner.invoke(app, list(args))


def write_cfg(root: Path, extra: str = "", course_extra: str = "") -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n"
        f"[sync]\nroot = '{root}'\n{extra}"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n{course_extra}"
    )
    return cfg


@pytest.fixture
def env(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    write_cfg(root)
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return root / "Example Course"


# -- CLI ------------------------------------------------------------------------


@respx.mock
def test_cli_full_cycle(env: Path, tmp_path: Path) -> None:
    install(respx.mock)
    dry = run("--json", "sync", "--dry-run")
    assert dry.exit_code == 0, dry.output
    data = json.loads(dry.output)
    assert data["missing_courses"] == []
    real = run("sync")
    assert real.exit_code == 0, real.output
    again = json.loads(run("--json", "sync").output)
    assert again["actions"] == [] and again["summary"] == {"unchanged": 3}
    # state has folder types, schema 2, config 0600
    con = sqlite3.connect(paths.state_db())
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    types = {r[0] for r in con.execute("SELECT folder_type FROM files")}
    assert "StandardFolder" in types, types
    con.close()
    # reorganize: nothing to move right after a sync
    reorg = run("--json", "reorganize", "--dry-run")
    assert reorg.exit_code == 0, reorg.output
    assert json.loads(reorg.output) == []
    # open: path in JSON
    opened = run("--json", "open", "Quantum")
    assert opened.exit_code == 0, opened.output
    assert json.loads(opened.output)["path"].endswith("Example Course")
    assert run("open", "nonexistent-course").exit_code != 0
    # dates --ics
    ics = tmp_path / "d.ics"
    out = run("dates", "--ics", str(ics))
    assert out.exit_code == 0, out.output
    text = ics.read_text()
    assert text.startswith("BEGIN:VCALENDAR") and "END:VCALENDAR" in text


@respx.mock
def test_cli_missing_course_and_planned_error_exit_code(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    write_cfg(
        root,
        course_extra=(
            "[[courses." + COURSE + ".rules]]\nid = 'x'\nmatch = { name_regex = 'Orga' }\n"
            "target = 'DoesNotExist'\n"
            "[courses.ffffffffffffffffffffffffffffffff]\nname = 'Gone'\ntarget = 'Gone'\n"
        ),
    )
    install(respx.mock)
    res = run("--json", "sync")
    data = json.loads(res.output)
    assert [c["name"] for c in data["missing_courses"]] == ["Gone"]
    assert res.exit_code == 1, "planned ERROR (missing target folder) must give exit 1"


@respx.mock
def test_cli_strips_escape_sequences(env: Path) -> None:
    install(respx.mock)
    respx.get(f"{API}/users/u1/courses").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "courses",
                        "id": COURSE,
                        "attributes": {"title": "Evil\u001b]8;;https://x\u001b\\Course\u001b[2J"},
                        "relationships": {
                            "start-semester": {"data": {"type": "semesters", "id": "sem-now"}},
                            "end-semester": {"data": {"type": "semesters", "id": "sem-now"}},
                        },
                    }
                ],
                "meta": {"page": {"total": 1}},
            },
        )
    )
    res = run("courses")
    assert res.exit_code == 0, res.output
    assert "\x1b" not in res.output and "Evil" in res.output


def test_cli_bad_profile_env(env: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIP_ROUTER_PROFILE", "../x")
    res = run("doctor", "--offline")
    assert res.exit_code == 2, res.output


def test_cli_own_keywords_in_rules_test(root: Path, monkeypatch) -> None:
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    write_cfg(root, extra='[sync.keywords]\nexercises = ["Werkstatt"]\n')
    res = run("rules", "test", "Quantum", "Werkstatt 2.pdf")
    assert res.exit_code == 0, res.output
    assert "Uebungen" in res.output, res.output


# -- TUI --------------------------------------------------------------------------


async def wait_for(pilot, cond, tries: int = 80) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if cond():
            return
    raise AssertionError("condition not reached")


@respx.mock
async def test_tui_offline_home_says_so(env: Path) -> None:
    from textual.widgets import Static

    from studip_router.tui import StudipRouterApp

    respx.route().mock(side_effect=httpx.ConnectError("down"))
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        facts = app.screen.query_one("#facts", Static)
        await wait_for(pilot, lambda: "not reachable" in str(facts.render()))
        assert app.status_fetching is False


@respx.mock
async def test_tui_news_x_and_merge_does_not_write(env: Path) -> None:
    from studip_router.state import State
    from studip_router.tui import StudipRouterApp
    from studip_router.tui.views import MergeView, NewsView

    def read_count(ids: list[str]) -> int:
        with State(paths.state_db()) as state:
            return len(state.news_seen(ids))

    install(respx.mock)
    write_cfg(
        env.parent,
        course_extra="merge = { source = 'Skript : Alle Folien', output = 'All.pdf' }\n",
    )
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("a")
        await wait_for(pilot, lambda: isinstance(app.screen, NewsView) and app.screen.items)
        view = app.screen
        n = len(view.items)
        ids = [item.id for item in view.items]
        assert read_count(ids) == 0  # opening does not mark anything as read
        await pilot.press("x")
        await pilot.pause(0.3)
        assert view.marked and read_count(ids) == n
        await pilot.press("escape")
        await pilot.press("m")
        await wait_for(pilot, lambda: isinstance(app.screen, MergeView))
        await pilot.pause(0.5)
        assert not (env / "Skript : Alle Folien" / "All.pdf").exists()
        assert n >= 1


async def test_tui_setup_escape_goes_back(root: Path, monkeypatch) -> None:
    from textual.widgets import Input

    from studip_router.tui import ConfirmScreen, StudipRouterApp
    from studip_router.tui.setup import SetupScreen

    monkeypatch.setenv("HOME", str(root.parent))
    app = StudipRouterApp()
    async with app.run_test(size=(120, 50)) as pilot:
        assert isinstance(app.screen, SetupScreen)
        s = app.screen
        s.query_one("#step1").display = False
        s.query_one("#step2").display = True
        await pilot.press("escape")
        assert s.query_one("#step1").display and not s.query_one("#step2").display
        assert s.query_one("#url", Input).has_focus
        await pilot.press("escape")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("n")
        assert isinstance(app.screen, SetupScreen) and app.is_running


async def test_tui_settings_after_invalid_config_edit(env: Path, monkeypatch) -> None:
    from studip_router.tui import HomeScreen, StudipRouterApp
    from studip_router.tui import settings as settings_mod
    from studip_router.tui.settings import SettingsScreen

    def broken_editor(path: Path) -> None:
        path.write_text("this is = = not toml")
        return None

    monkeypatch.setattr(settings_mod, "open_in_editor", broken_editor)
    import contextlib

    app = StudipRouterApp()
    monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("comma")
        assert isinstance(app.screen, SettingsScreen)
        await pilot.press("e")
        await pilot.pause(0.3)
        assert isinstance(app.screen, HomeScreen), type(app.screen)
        await pilot.press("l", "enter")  # used to crash with AttributeError
        await pilot.pause(0.2)
        assert app.is_running


async def test_tui_light_theme_switch(env: Path) -> None:
    from studip_router.tui import StudipRouterApp, widgets

    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        app.theme = "textual-light"
        await pilot.pause()
        assert widgets._light
        app.theme = "textual-dark"
        await pilot.pause()
        assert not widgets._light
