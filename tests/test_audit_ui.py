"""Keyboard and resize regressions for the audit's UI workflows."""

from __future__ import annotations

from pathlib import Path

import pytest
import respx
from fake_studip import BASE, COURSE, install
from textual.widgets import DataTable, Input, Static

from studip_router import paths
from studip_router.config import load
from studip_router.tui import StudipRouterApp
from studip_router.tui.settings import SettingRow
from studip_router.tui.views import NewsView, RulesView, SyncView, clip
from studip_router.tui.widgets import DetailScreen, TextScreen


@pytest.fixture
def configured(root: Path, monkeypatch):
    path = paths.config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[server]\nbase_url="{BASE}"\nusername="u"\n[sync]\nroot="{root}"\n'
        f'[courses.{COURSE}]\nname="Course"\ntarget="Example Course"\n'
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return path


async def settle(pilot, predicate):
    for _ in range(80):
        await pilot.pause(0.025)
        if predicate():
            return
    raise AssertionError("UI did not reach expected state")


@pytest.mark.parametrize("size", [(40, 20), (80, 24), (120, 36)])
async def test_dialog_fits_and_resizes(configured, size):
    app = StudipRouterApp()
    async with app.run_test(size=size) as pilot:
        app.push_screen(TextScreen("Folder", "long folder/" * 15, "Choose your target"))
        await pilot.pause()
        for width in [size[0], 40, 80]:
            await pilot.resize_terminal(width, 24)
            await pilot.pause()
            dialog = app.screen.query_one(".dialog")
            field = app.screen.query_one(Input)
            assert dialog.region.right <= width
            assert field.region.right <= width
        await pilot.press("escape")


@respx.mock
async def test_preview_filter_and_copy_preserve_full_paths(configured, monkeypatch):
    install(respx.mock)
    app = StudipRouterApp()
    copied = []
    monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("p")
        await settle(pilot, lambda: isinstance(app.screen, SyncView) and app.screen.plan)
        view = app.screen
        await pilot.press("d")
        assert isinstance(app.screen, DetailScreen)
        assert "Target:" in app.screen.body
        await pilot.press("ctrl+y")
        assert copied and copied[0] == app.screen.body
        await pilot.press("escape", "slash")
        field = view.query_one("#plan-filter", Input)
        field.value = "Orga"
        await pilot.pause()
        assert view.query_one(DataTable).row_count == 1
        await pilot.press("enter")
        assert view.query_one(DataTable).has_focus
        await pilot.press("slash")
        field.value = ""
        await pilot.press("escape")
        assert not field.display and view.query_one(DataTable).row_count == 3


async def test_setting_undo_and_fractional_size(configured):
    app = StudipRouterApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("comma")
        row = next(r for r in app.screen.query(SettingRow) if r.setting.label == "size limit")
        row.focus()
        await pilot.press("enter")
        assert isinstance(app.screen, TextScreen)
        app.screen.query_one(Input).value = "12.75"
        await pilot.press("enter")
        assert load(configured).exclude.max_size_mb == 12.75
        assert row.value == 12.75
        await pilot.press("u")
        assert load(configured).exclude.max_size_mb == 200
        assert row.value == 200


@respx.mock
async def test_news_pane_switch_on_narrow_terminal(configured):
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.press("a")
        assert isinstance(app.screen, NewsView)
        view = app.screen
        assert view.query_one("#news-list").display
        assert not view.query_one("#news-body").display
        await pilot.press("v")
        assert not view.query_one("#news-list").display
        assert view.query_one("#news-body").display
        await pilot.resize_terminal(100, 30)
        await pilot.pause()
        assert view.query_one("#news-list").display and view.query_one("#news-body").display


@respx.mock
async def test_rule_metadata_simulation(configured):
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("r")
        assert isinstance(app.screen, RulesView)
        view = app.screen
        view.query_one("#test-course", Input).value = "Course"
        view.query_one("#test-name", Input).value = "test.pdf"
        view.query_one("#test-size", Input).value = "nan"
        await pilot.pause()
        assert "Invalid simulation metadata" in str(view.query_one("#test-result", Static).render())
        view.query_one("#test-size", Input).value = "1.5"
        await pilot.pause()
        assert "→" in str(view.query_one("#test-result", Static).render())


def test_unicode_clipping_respects_terminal_cells():
    from rich.cells import cell_len

    result = clip("文書/講義資料/very-long-name.pdf", 24)
    assert cell_len(result) <= 24 and result.endswith(".pdf")


@respx.mock
async def test_large_preview_pages_do_not_hide_the_sync_plan(configured):
    from copy import deepcopy

    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press("p")
        await settle(pilot, lambda: isinstance(app.screen, SyncView) and app.screen.plan)
        view = app.screen
        plan = deepcopy(view.plan)
        plan.plans[0].actions *= 400
        view.show_plan(plan)
        assert view.query_one(DataTable).row_count == 500
        assert len(plan.plans[0].actions) == 1200
        view.action_page(1)
        assert view.page == 1 and view.query_one(DataTable).row_count == 500
        view.action_page(1)
        assert view.page == 2 and view.query_one(DataTable).row_count == 200
