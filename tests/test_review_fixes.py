"""Regression tests for problems found in a review: each one reproduced the bug first."""

from __future__ import annotations

import io
import os
import stat
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import keyring.errors
import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, make_config, remote
from fake_studip import API, BASE, COURSE, install
from rich.console import Console
from textual.widgets import Input
from typer.testing import CliRunner

from studip_router import auth, paths
from studip_router.api import ApiError, AuthFailed, StudipClient
from studip_router.cli import app
from studip_router.config import ConfigError, parse
from studip_router.executor import execute
from studip_router.planner import Kind, plan_course
from studip_router.postprocess import PostprocessError, extract_zip
from studip_router.rename import RenameError, RenameOptions, target_name
from studip_router.state import SCHEMA_VERSION, State, StateError
from studip_router.suggest import auto_target
from studip_router.wizard import propose_target

runner = CliRunner()
SECRET = "S3cret-PW-4711"


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


# -- passwords never show up in crash output ------------------------------------------


@respx.mock
def test_html_instead_of_json_is_an_api_error() -> None:
    respx.get(f"{API}/users/me").mock(
        return_value=httpx.Response(200, html="<html>Shibboleth login</html>")
    )
    with pytest.raises(ApiError, match="SSO"):
        StudipClient(BASE, "u", SECRET).me()


@respx.mock
async def test_setup_reports_sso_page_instead_of_crashing(root: Path, monkeypatch) -> None:
    from studip_router.tui import StudipRouterApp
    from studip_router.tui.setup import SetupScreen

    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(200, html="<html>SSO</html>"))
    monkeypatch.setenv("HOME", str(root.parent))
    app = StudipRouterApp()
    async with app.run_test(size=(120, 50)) as pilot:
        for field, val in (("url", "studip.example.edu"), ("username", "jdoe")):
            app.screen.query_one(f"#{field}", Input).value = val
        app.screen.query_one("#password", Input).value = SECRET
        await pilot.click("#connect")
        await pilot.pause(0.5)
        assert isinstance(app.screen, SetupScreen)
        assert "SSO" in str(app.screen.query_one("#status").render())


def test_tui_crash_traceback_has_no_locals() -> None:
    from studip_router.tui import StudipRouterApp

    app = StudipRouterApp()

    def leaky(password: str) -> None:
        raise RuntimeError("boom")

    try:
        leaky(SECRET)
    except RuntimeError:
        app._close_messages_no_wait = lambda: None  # type: ignore[method-assign]
        app._fatal_error()
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    for renderable in app._exit_renderables:
        console.print(renderable)
    assert "RuntimeError" in buf.getvalue()
    assert SECRET not in buf.getvalue()


def _no_keyring(*_args: object) -> None:
    raise keyring.errors.NoKeyringError("No recommended backend was available")


def test_missing_keychain_is_a_clear_error_without_the_password(monkeypatch) -> None:
    monkeypatch.setattr("keyring.set_password", _no_keyring)
    with pytest.raises(auth.AuthError) as exc:
        auth.set_password(BASE, "jdoe", SECRET)
    assert "STUDIP_ROUTER_PASSWORD" in str(exc.value)
    assert SECRET not in str(exc.value)


def test_pdf_password_without_keychain_exits_cleanly(setup: Path, monkeypatch) -> None:
    monkeypatch.setattr("keyring.set_password", _no_keyring)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: SECRET)
    result = runner.invoke(app, ["pdf-password", "set", "Q"])
    assert result.exit_code == 3
    assert "keychain" in result.output and SECRET not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


# -- overwrite never clobbers an edit made after planning ------------------------------


def test_overwrite_rechecks_local_edits_at_run_time(root: Path, state) -> None:
    cfg = make_config(root, on_update="overwrite")
    course = cfg.courses[COURSE_ID]
    first = remote("notes.pdf", size=3)
    execute(plan_course(cfg, course, [first], {}), state, FakeDownloads({first.id: b"old"}), 1)
    changed = remote("notes.pdf", size=3, chdate=datetime(2026, 10, 5, tzinfo=UTC))
    plan = plan_course(cfg, course, [changed], state.for_course(COURSE_ID))
    assert [a.kind for a in plan.actions] == [Kind.OVERWRITE]
    target = root / "Example Course" / "Sonstiges" / "notes.pdf"
    target.write_bytes(b"my annotations")  # edited while the preview was open
    outcome = execute(plan, state, FakeDownloads({changed.id: b"new"}), 2)
    assert target.read_bytes() == b"my annotations"
    assert len(outcome.failed) == 1 and "changed locally" in outcome.failed[0][1]
    assert not list(target.parent.glob("*.part"))
    # the next sync keeps the edit and stores the update next to it
    again = plan_course(cfg, course, [changed], state.for_course(COURSE_ID))
    assert [(a.kind, a.dest.name) for a in again.actions] == [(Kind.NEW_VERSION, "notes_v2.pdf")]


# -- adopting hand-downloaded files ---------------------------------------------------


def test_lone_numbered_file_is_not_adopted_as_another_file(cfg, root: Path) -> None:
    (root / "Example Course" / "Uebungen" / "Blatt_01.pdf").write_bytes(b"X" * 100)
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("Blatt.pdf", size=100)], {})
    assert [a.kind for a in plan.actions] == [Kind.DOWNLOAD]


# -- one broken file never stops the run; a rejected login does ------------------------


def test_unexpected_error_fails_only_that_file(cfg, state) -> None:
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("a.pdf"), remote("b.pdf")], {})

    def download(fid, sink):
        if fid == "file-a.pdf":
            raise ValueError("pypdf-ish surprise")
        sink(b"abc")

    outcome = execute(plan, state, download, run_id=1)
    assert [a.file.name for a, _ in outcome.failed] == ["a.pdf"]
    assert state.get("file-b.pdf") is not None


def test_rejected_login_stops_the_run(cfg, state) -> None:
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("a.pdf"), remote("b.pdf")], {})
    calls = []

    def download(fid, sink):
        calls.append(fid)
        raise AuthFailed("authentication failed (HTTP 401)")

    with pytest.raises(AuthFailed):
        execute(plan, state, download, run_id=1)
    assert len(calls) == 1


@respx.mock
def test_news_does_not_repeat_a_rejected_login(root: Path, monkeypatch) -> None:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    ids = [f"{i:032x}" for i in range(1, 6)]
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        + "".join(f"[courses.{c}]\nname = 'C{c[-1]}'\ntarget = 'Example Course'\n" for c in ids)
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "wrong")
    route = respx.get(url__regex=rf"{API}/courses/.*/news").mock(return_value=httpx.Response(401))
    result = runner.invoke(app, ["news"])
    assert result.exit_code == 3
    assert route.call_count == 1


# -- configuration limits ---------------------------------------------------------------


def _raw(root: str, **course: object) -> dict:
    return {
        "server": {"base_url": "https://studip.example.edu", "username": "x"},
        "sync": {"root": root},
        "courses": {"c1": {"target": "t", **course}},
    }


@pytest.mark.parametrize("root", ["/", "~", "/etc", "/usr/share/x"])
def test_unsafe_roots_are_rejected(root: str) -> None:
    with pytest.raises(ConfigError, match=r"sync\.root"):
        parse(_raw(root), Path("c.toml"))


@pytest.mark.parametrize("target", ["/", "~", "/etc/x"])
def test_unsafe_course_targets_are_rejected(tmp_path: Path, target: str) -> None:
    with pytest.raises(ConfigError, match=r"courses\.c1\.target"):
        parse(_raw(str(tmp_path), target=target), Path("c.toml"))


def test_system_folder_behind_a_symlink_is_rejected(tmp_path: Path) -> None:
    # like /etc -> /private/etc on macOS: the link itself is outside the list
    link = tmp_path / "etc-link"
    link.symlink_to("/etc")
    with pytest.raises(ConfigError, match=r"sync\.root"):
        parse(_raw(str(link)), Path("c.toml"))
    from studip_router.config import unsafe_folder

    assert unsafe_folder("/private/etc/x") is not None


def test_absolute_target_outside_the_root_is_allowed(tmp_path: Path) -> None:
    cfg = parse(_raw(str(tmp_path / "a"), target=str(tmp_path / "b")), Path("c.toml"))
    assert cfg.course_dir(cfg.courses["c1"]) == tmp_path / "b"


def test_template_attribute_access_is_refused() -> None:
    raw = _raw("/tmp/x", rules=[{"match": {}, "target": "a", "rename": "{orig_name.__class__}"}])
    with pytest.raises(ConfigError, match="attribute"):
        parse(raw, Path("c.toml"))
    with pytest.raises(RenameError):
        target_name(remote("x.pdf"), "{orig_name.__class__}", RenameOptions())


# -- heuristics ---------------------------------------------------------------------------


def test_follow_up_course_is_not_matched_to_last_semesters_folder() -> None:
    assert propose_target("Analysis II", ["Analysis I"]) is None
    assert propose_target("Lineare Algebra II", ["Lineare Algebra I"]) is None
    assert propose_target("Analysis II", ["Analysis I", "Analysis II"]) == "Analysis II"
    assert propose_target("Analysis II", ["Analysis"]) == "Analysis"
    assert propose_target("Lineare Algebra I", ["Lineare Algebra 1"]) == "Lineare Algebra 1"


@pytest.mark.parametrize(
    "folder", ["Code Examples", "Beispiele/Examples", "Cheat Sheets", "Scripts"]
)
def test_false_friends_are_not_routed(folder: str) -> None:
    assert auto_target(folder, ["Folien", "Klausur", "Skript", "Übungen"]) is None


@pytest.mark.parametrize(
    ("folder", "expected"),
    [("Altklausuren", "Klausur"), ("Übungsblätter", "Übungen"), ("Skripte", "Skript")],
)
def test_real_categories_still_route(folder: str, expected: str) -> None:
    found = auto_target(folder, ["Klausur", "Skript", "Übungen"])
    assert found is not None and found[1] == expected


# -- archives -----------------------------------------------------------------------------


def test_unzip_never_removes_existing_folders(tmp_path: Path) -> None:
    archive = tmp_path / "Material.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "a")
    mine = tmp_path / "Material.unzip-tmp"
    mine.mkdir()
    (mine / "precious.txt").write_text("user data")
    assert extract_zip(archive) == tmp_path / "Material"
    assert (mine / "precious.txt").read_text() == "user data"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "Material",
        "Material.unzip-tmp",
        "Material.zip",
    ]


def test_zip_bomb_ratio_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\0" * (20 << 20))
    with pytest.raises(PostprocessError, match="ratio"):
        extract_zip(archive)
    assert [p.name for p in tmp_path.iterdir()] == ["bomb.zip"]


def test_zip_member_names_are_made_safe_and_kept_apart(tmp_path: Path) -> None:
    archive = tmp_path / "names.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("dir/A.txt", "upper")
        zf.writestr("dir/a.txt", "lower")
        zf.writestr("bad\x01name?.txt", "x")
    folder = extract_zip(archive)
    assert folder is not None
    names = sorted(p.name for p in (folder / "dir").iterdir())
    assert len(names) == 2  # nothing overwritten on case-insensitive file systems
    assert (folder / "badname_.txt").read_text() == "x"


@pytest.mark.parametrize("name", ["C:/evil.txt", "/abs.txt", "../up.txt"])
def test_zip_slip_variants_are_refused(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(name, "x")
    with pytest.raises(PostprocessError):
        extract_zip(archive)


# -- state ------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_state_and_log_are_private(tmp_path: Path) -> None:
    old = os.umask(0o022)
    try:
        with State(tmp_path / "st" / "state.sqlite"):
            pass
        from studip_router.report import setup_file_logging

        setup_file_logging(tmp_path / "st" / "x.log", verbose=False)
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "st").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "st" / "state.sqlite").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "st" / "x.log").stat().st_mode) == 0o600


def test_damaged_state_has_a_helpful_error(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite"
    db.write_bytes(b"not a database" * 100)
    with pytest.raises(StateError, match="Rename it"):
        State(db)


def test_state_from_a_newer_version_is_refused(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "state.sqlite"
    con = sqlite3.connect(db)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    con.close()
    with pytest.raises(StateError, match="newer"):
        State(db)


def test_cli_reports_damaged_state_without_traceback(setup: Path, capsys) -> None:
    from studip_router.cli import main

    paths.state_db().parent.mkdir(parents=True, exist_ok=True)
    paths.state_db().write_bytes(b"garbage" * 200)
    with pytest.raises(SystemExit) as exc:
        sys.argv = ["studip", "history"]
        main()
    assert exc.value.code == 4
    assert "damaged" in capsys.readouterr().err


# -- dates ------------------------------------------------------------------------------


def test_dates_without_offset_do_not_break_the_comparison() -> None:
    from studip_router.dates import _parse_dt

    parsed = _parse_dt("2026-11-02T10:00:00")
    assert parsed is not None and parsed.tzinfo is not None
    assert parsed > datetime(2026, 1, 1, tzinfo=UTC)


# -- confirm dialog ---------------------------------------------------------------------


@respx.mock
async def test_double_enter_does_not_confirm_a_sync(setup: Path) -> None:
    from studip_router.tui import ConfirmScreen, StudipRouterApp

    install(respx.mock)
    app = StudipRouterApp()
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("s")
        for _ in range(60):
            await pilot.pause(0.05)
            if isinstance(app.screen, ConfirmScreen):
                break
        assert isinstance(app.screen, ConfirmScreen)
        app.screen._opened = time.monotonic()  # the accidental second Enter comes at once
        await pilot.press("enter")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.pause(0.3)  # the button's own press animation must be over
        app.screen._opened -= 1
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, ConfirmScreen)


def test_scheduler_runs_this_installation(monkeypatch, tmp_path: Path) -> None:
    from studip_router import schedule

    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "studip").write_text("#!/bin/sh\n")
    other = tmp_path / "other"
    other.mkdir()
    (other / "studip").write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    monkeypatch.setenv("PATH", str(other))
    assert schedule.executable() == str(venv_bin / "studip")
