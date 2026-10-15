"""Text from Stud.IP (titles, file names, announcements) is shown, never interpreted."""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
import respx
from fake_studip import API, BASE, COURSE, FILES, install
from rich.console import Console
from typer.testing import CliRunner

from studip_router import cli, paths
from studip_router.cli import app

runner = CliRunner()
EVIL = (
    "Abgabe bis [/b] Freitag, [link=https://phish.example/login]https://studip.example.edu[/link]"
)


@pytest.fixture
def setup(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Q'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "test-only")
    return root / "Example Course"


def _news(content: str, title: str = "Info [/i]") -> httpx.Response:
    item = {
        "type": "news",
        "id": "n9",
        "attributes": {
            "title": title,
            "content": content,
            "publication-start": "2026-10-12T09:00:00+02:00",
        },
    }
    return httpx.Response(
        200, json={"data": [item], "meta": {"page": {"offset": 0, "limit": 100, "total": 1}}}
    )


@pytest.fixture
def terminal(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Capture what an interactive terminal would receive (with escape sequences)."""
    buf = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=buf, force_terminal=True, highlight=False, width=200)
    )
    return buf


@respx.mock
def test_announcements_cannot_crash_or_inject_links(setup: Path, terminal: io.StringIO) -> None:
    install(respx.mock)
    respx.get(f"{API}/courses/{COURSE}/news").mock(return_value=_news(EVIL))
    result = runner.invoke(app, ["news"])
    assert result.exception is None, result.output
    out = terminal.getvalue()
    assert "\x1b]8;" not in out  # no terminal hyperlink with a disguised target
    assert "[link=https://phish.example/login]" in out  # shown as plain text
    assert "[/b]" in out and "Info [/i]" in out


@respx.mock
def test_markup_in_file_and_folder_names_is_shown_as_is(setup: Path, monkeypatch) -> None:
    files = dict(FILES)
    files["f-odd"] = ("Lösung [b] [v2].pdf", "root", b"%PDF-odd")
    monkeypatch.setattr("fake_studip.FILES", files)
    install(respx.mock)
    buf = io.StringIO()
    monkeypatch.setattr(cli, "Console", lambda **kw: Console(file=buf, width=200, **kw))
    result = runner.invoke(app, ["sync", "--dry-run"])
    assert result.exception is None, result.output
    assert "Lösung [b] [v2].pdf" in buf.getvalue()


@respx.mock
async def test_tui_announcement_view_shows_markup_as_text(setup: Path) -> None:
    from textual.widgets import Static

    from studip_router.tui import StudipRouterApp
    from studip_router.tui.views import NewsView

    install(respx.mock)
    respx.get(f"{API}/courses/{COURSE}/news").mock(return_value=_news(EVIL))
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("a")
        for _ in range(40):
            await pilot.pause(0.05)
            if isinstance(app.screen, NewsView) and app.screen.items:
                break
        assert isinstance(app.screen, NewsView)
        await pilot.press("down", "up")
        await pilot.pause(0.2)
        text = str(app.screen.query_one("#news-text", Static).render())
        assert "[/b]" in text and "[link=https://phish.example/login]" in text


@respx.mock
async def test_tui_sync_preview_survives_markup_in_names(setup: Path, monkeypatch) -> None:
    from textual.widgets import DataTable

    from studip_router.tui import StudipRouterApp
    from studip_router.tui.views import SyncView

    files = dict(FILES)
    files["f-odd"] = ("[/b] Blatt [link=x]1[/link].pdf", "folder-sheets", b"%PDF-odd")
    monkeypatch.setattr("fake_studip.FILES", files)
    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(140, 36)) as pilot:
        await pilot.press("p")
        for _ in range(60):
            await pilot.pause(0.05)
            if isinstance(app.screen, SyncView) and app.screen.plan is not None:
                break
        table = app.screen.query_one(DataTable)
        cells = [str(table.get_cell_at((r, 2))) for r in range(table.row_count)]
        assert any("[/b] Blatt [link=x]1[/link].pdf" in c for c in cells), cells
