"""Findings of the check against a real Stud.IP before 0.5.0."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import respx
from conftest import COURSE_ID, remote
from fake_studip import BASE, COURSE, install
from rich.console import Console

from studip_router import paths, report
from studip_router.config import CourseConfig
from studip_router.planner import Action, CoursePlan, Kind, SkipReason
from studip_router.tui import StudipRouterApp
from studip_router.tui.views import SyncView


def _plan(actions: list[Action]) -> CoursePlan:
    course = CourseConfig(id=COURSE_ID, name="Quantum Cooking", target="Quantum Cooking")
    return CoursePlan(course, Path("/tmp/uni with spaces/Quantum Cooking"), actions)


def _skip(name: str, folder: str, reason: str = SkipReason.EXCLUDED) -> Action:
    return Action(Kind.SKIP, COURSE_ID, remote(name, folder=folder), reason=reason)


def _render(plan: CoursePlan, verbose: bool) -> str:
    table = report.plan_table(plan, verbose)
    assert table is not None
    out = io.StringIO()
    Console(file=out, width=200).print(table)
    return out.getvalue()


def test_verbose_groups_many_skips_of_one_folder() -> None:
    images = [_skip(f"img{i}.png", "CoursewareImport 1/Unknown") for i in range(45)]
    few = [_skip("a.mp4", "Videos"), _skip("b.mp4", "Videos")]
    text = _render(_plan(images + few + [_skip(".DS_Store", "Videos", "system file")]), True)
    assert "CoursewareImport 1/Unknown/ (45 files)" in text
    assert "img0.png" not in text
    assert "Videos/a.mp4" in text and "Videos/b.mp4" in text  # two are listed one by one
    assert "Videos/.DS_Store" in text


def test_non_verbose_plan_shows_no_skips() -> None:
    assert report.plan_table(_plan([_skip("img.png", "X")] * 5), False) is None


def test_course_heading_is_not_wrapped_inside_the_path() -> None:
    out = io.StringIO()
    Console(file=out, width=30).print(report.plan_heading(_plan([])), soft_wrap=True)
    assert "/tmp/uni with spaces/Quantum Cooking" in out.getvalue()


@pytest.fixture
def ruled_config(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Quantum Cooking'\ntarget = 'Example Course'\n"
        f"[[courses.{COURSE}.rules]]\nid = 'mine'\n"
        "match = { name_glob = 'lecture-*.pdf' }\ntarget = 'Uebungen'\n"
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
async def test_no_report_for_a_file_placed_by_your_own_rule(ruled_config: Path) -> None:
    from textual.widgets import DataTable

    from studip_router.tui.widgets import ReportScreen

    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("p")
        await _wait(pilot, lambda: isinstance(app.screen, SyncView) and app.screen.plan)
        view = app.screen
        rows = view.row_actions
        index = next(i for i, a in enumerate(rows) if a.file and a.file.name == "lecture-1.pdf")
        assert rows[index].route and rows[index].route.rule_id == "mine"
        view.query_one(DataTable).move_cursor(row=index)
        await pilot.press("b")
        await pilot.pause(0.3)
        assert not isinstance(app.screen, ReportScreen)
        assert any("your own rule" in str(n.message) for n in app._notifications)
