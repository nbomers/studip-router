"""The points left open after the 0.4.1 review (0.4.2)."""

from __future__ import annotations

import contextlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, make_config, remote
from fake_studip import install
from typer.testing import CliRunner

from studip_router import paths
from studip_router.cli import app as cli_app
from studip_router.cli import hoist_global_options
from studip_router.executor import execute
from studip_router.planner import plan_course
from studip_router.reorganize import plan_reorganize
from studip_router.tui import HomeScreen, StudipRouterApp
from studip_router.tui import app as app_mod
from studip_router.tui import settings as settings_mod
from studip_router.tui.settings import SettingsScreen
from studip_router.tui.views import HistoryView, SyncView

# -- folder type of files synced before 0.4.2 ----------------------------------------


def test_sync_fills_in_the_folder_type_of_old_records(state, root: Path) -> None:
    cfg = make_config(root)
    (root / "Example Course" / "Loesungen").mkdir()
    f = replace(remote("Loesung_3.pdf", folder="Gruppe A"), folder_type="HomeworkFolder")
    execute(plan_course(cfg, cfg.courses[COURSE_ID], [f], {}), state, FakeDownloads(), 1)
    state.upsert(replace(state.get(f.id), folder_type=""))  # as synced by 0.4.1

    plan = plan_course(cfg, cfg.courses[COURSE_ID], [f], state.for_course(COURSE_ID))
    assert plan.folder_types == {f.id: "HomeworkFolder"}
    state.set_folder_types(plan.folder_types)
    assert state.get(f.id).folder_type == "HomeworkFolder"
    assert plan_reorganize(cfg, state) == []  # same place as before: nothing to move

    # a known type is never overwritten, and records that have one are not listed
    state.set_folder_types({f.id: "StandardFolder"})
    assert state.get(f.id).folder_type == "HomeworkFolder"
    again = plan_course(cfg, cfg.courses[COURSE_ID], [f], state.for_course(COURSE_ID))
    assert again.folder_types == {}


# -- status thread after the app quit ------------------------------------------------


def test_status_thread_does_not_call_into_a_closed_app() -> None:
    app = StudipRouterApp()  # never run: no event loop
    called: list[int] = []
    app_mod._to_ui(app, lambda: called.append(1))
    assert called == []


# -- completions in the package ------------------------------------------------------


@pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
def test_completion_scripts_ship_with_the_package(shell: str) -> None:
    result = CliRunner().invoke(cli_app, ["completion", shell])
    assert result.exit_code == 0
    assert "studip _complete courses" in result.output
    assert "completion" in result.output  # the new command completes itself


def test_completion_rejects_unknown_shells() -> None:
    result = CliRunner().invoke(cli_app, ["completion", "tcsh"])
    assert result.exit_code == 2


# -- U13: global options after the command, history -l ---------------------------------


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["sync", "--json"], ["--json", "sync"]),
        (["sync", "-p", "uni", "-n"], ["-p", "uni", "sync", "-n"]),
        (["--profile=uni", "status"], ["--profile=uni", "status"]),
        (["status", "--profile=uni"], ["--profile=uni", "status"]),
        (["-p", "uni", "--json", "history"], ["-p", "uni", "--json", "history"]),
        (["auth", "login", "--json"], ["--json", "auth", "login"]),
        (["rules", "test", "--", "--json"], ["rules", "test", "--", "--json"]),
        (["sync", "-p"], ["sync", "-p"]),  # no value: click reports it
        ([], []),
    ],
)
def test_global_options_may_follow_the_command(args: list[str], expected: list[str]) -> None:
    assert hoist_global_options(args) == expected


def test_json_after_the_command_works_end_to_end(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from studip_router.cli import main

    monkeypatch.setattr(sys, "argv", ["studip", "history", "--json", "-l", "5"])
    with contextlib.suppress(SystemExit):
        main()
    assert capsys.readouterr().out.strip() == "[]"


def test_history_n_still_works_but_points_to_l() -> None:
    result = CliRunner().invoke(cli_app, ["history", "-n", "3"])
    assert result.exit_code == 0
    assert "history -l" in result.output


# -- settings with a config that turned invalid ----------------------------------------


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
async def test_invalid_config_from_settings_opened_in_a_view_goes_home(
    course_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(respx.mock)
    monkeypatch.setattr(
        settings_mod, "open_in_editor", lambda path: path.write_text("not = = toml")
    )
    app = StudipRouterApp()
    monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
    async with app.run_test(size=(120, 50)) as pilot:
        await pilot.press("w")
        await _wait(pilot, lambda: isinstance(app.screen, HistoryView))
        await pilot.press("comma")
        await _wait(pilot, lambda: isinstance(app.screen, SettingsScreen))
        await pilot.press("e")
        await _wait(pilot, lambda: isinstance(app.screen, HomeScreen))
        assert not any(isinstance(s, HistoryView) for s in app.screen_stack)
        assert app.is_running


# -- U12: keys inside views no longer reuse the home screen's letters -------------------


def _keys(screen: type) -> dict[str, str]:
    return {b.key: b.action for b in screen.BINDINGS if b.show}


def test_view_keys_do_not_reuse_home_letters_in_the_footer() -> None:
    assert "r" not in _keys(SyncView) and _keys(SyncView)["ctrl+r"] == "refresh"
    history = _keys(HistoryView)
    assert "o" not in history and "r" not in history
    assert history["f"] == "reveal"
