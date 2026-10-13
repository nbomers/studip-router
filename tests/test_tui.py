from __future__ import annotations

from pathlib import Path

import pytest
import respx
from fake_studip import BASE, COURSE, install
from textual.widgets import DataTable, OptionList, SelectionList

from studip_router import paths
from studip_router.tui import ConfirmScreen, StudipRouterApp


@pytest.fixture
def course_dir(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return root / "Example Course"


async def _wait_for(pilot, condition, tries: int = 50) -> None:
    for _ in range(tries):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition not reached")


@respx.mock
async def test_sync_preview_confirm_and_history(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 40)) as pilot:
        assert isinstance(app.screen.query_one("#menu"), OptionList)
        await pilot.press("1")  # Sync
        table = app.screen.query_one(DataTable)
        await _wait_for(pilot, lambda: table.row_count == 3)
        assert not (course_dir / "Sonstiges" / "Orga.pdf").exists()  # preview only

        await pilot.press("enter")
        await _wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await _wait_for(pilot, lambda: (course_dir / "Sonstiges" / "Orga.pdf").exists())
        await _wait_for(pilot, lambda: not app.screen.running)

        await pilot.press("escape", "3")  # What's new
        history = app.screen.query_one(DataTable)
        assert history.row_count == 3


@respx.mock
async def test_rule_suggestions_view(course_dir: Path) -> None:
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("5")
        selection = app.screen.query_one(SelectionList)
        await _wait_for(pilot, lambda: selection.option_count == 2)
        await pilot.press("w")
        await _wait_for(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await pilot.pause(0.1)
        assert "Übungsblätter*" in Path(paths.config_file()).read_text()
        assert len(app.cfg.courses[COURSE].rules) == 2


async def test_no_config_shows_hint(tmp_path: Path) -> None:
    app = StudipRouterApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.cfg is None
        await pilot.press("1")
        assert app.screen.__class__.__name__ == "MainScreen"
