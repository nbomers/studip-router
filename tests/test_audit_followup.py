"""Regression coverage for the numbered November audit, including failure recovery."""

from __future__ import annotations

import errno
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import COURSE_ID, make_config, remote

from studip_router import config_edit, config_io, fsutil, schedule
from studip_router.config import ConfigError, parse
from studip_router.dates import ChangeKind, diff
from studip_router.executor import execute
from studip_router.fsutil import move_no_clobber, path_problem
from studip_router.models import CourseDate
from studip_router.planner import plan_course
from studip_router.postprocess import merge_pdfs
from studip_router.suggest import auto_route, file_category, folder_category, local_category_dirs


@pytest.mark.parametrize("value", [42, True, 3.5, "wrong", ["wrong"]])
def test_bad_courses_is_a_config_error(root, value):
    with pytest.raises(ConfigError, match="courses: expected a table"):
        parse(
            {
                "server": {"base_url": "https://example.edu", "username": "u"},
                "sync": {"root": str(root)},
                "courses": value,
            },
            root / "config.toml",
        )


@pytest.mark.parametrize("key", ["base_url", "username"])
@pytest.mark.parametrize("value", ["", "   "])
def test_server_fields_are_not_blank(root, key, value):
    server = {"base_url": "https://example.edu", "username": "u", key: value}
    with pytest.raises(ConfigError, match=f"server.{key}"):
        parse({"server": server, "sync": {"root": str(root)}}, root / "config.toml")


def test_relative_root_is_stable_across_working_directories(tmp_path, monkeypatch):
    cfg_path = tmp_path / "cfg" / "config.toml"
    raw = {
        "server": {"base_url": "https://example.edu", "username": "u"},
        "sync": {"root": "  ../uni  "},
    }
    before = parse(raw, cfg_path).root.resolve()
    monkeypatch.chdir("/")
    assert parse(raw, cfg_path).root.resolve() == before == tmp_path / "uni"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_limits_rejected(root, value):
    with pytest.raises(ConfigError, match="max_size_mb"):
        make_config(root, exclude={"max_size_mb": value})


@pytest.mark.parametrize("name", ["Präsentationen", "Handouts", "Zusammenfassungen"])
def test_remote_and_local_synonyms_match(name):
    assert auto_route(name, "", "a.pdf", [name]) == ("slides", name, "folder name")


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Wallpapers", "reading"),
        ("Cheat sheets", "exercises"),
        ("Datasheets", "exercises"),
        ("Merkblätter", "exercises"),
        ("Deckblätter", "exercises"),
    ],
)
def test_local_false_friends_are_not_targets(name, category):
    assert not local_category_dirs([name], category)


def test_custom_words_and_deep_folders():
    assert auto_route("Werkstatt", "", "a.pdf", ["Werkstatt"], {"exercises": ("werkstatt",)}) == (
        "exercises",
        "Werkstatt",
        "folder name",
    )
    assert folder_category("Material/Woche 1/Dateien/Übungen") == "exercises"
    assert folder_category("Folien/Übungen") == "slides"  # stable parent precedence
    assert file_category("Flashcards.pdf") == "exercises"
    assert file_category("Lernkarten.pdf") == "exercises"
    assert folder_category("Quiz") == "exams"


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("Examples", False),
        ("Midterm", True),
        ("Testat", True),
        ("Prufung", True),
        ("Quiz", True),
    ],
)
def test_exam_vocabulary(category, expected):
    now = datetime.now(UTC)
    assert CourseDate("d", "c", "Course", now, now, category, "", False).is_exam is expected


def test_config_detects_external_changes_and_protects_backup(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("original")
    cfg_path.write_text("external edit")
    with pytest.raises(OSError, match="another editor"):
        config_io.save_config(cfg_path, "original", "replacement")
    assert cfg_path.read_text() == "external edit"
    assert not cfg_path.with_suffix(".toml.bak").exists()


def test_config_lock_rejects_competing_writer(root, tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        f'[server]\nbase_url="https://example.edu"\nusername="u"\n[sync]\nroot="{root}"\n'
    )
    with (
        fsutil.exclusive_lock(path.with_name("config.toml.lock")),
        pytest.raises(OSError, match="another process"),
    ):
        config_edit.set_value(path, ("sync", "notify"), False)
    config_edit.set_value(path, ("sync", "notify"), False)
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.with_suffix(".toml.bak")).st_mode & 0o777 == 0o600


@pytest.mark.parametrize("error", [errno.ENOSPC, errno.EDQUOT, errno.EROFS])
def test_failed_config_fsync_keeps_original(root, tmp_path, monkeypatch, error):
    path = tmp_path / "config.toml"
    text = f'[server]\nbase_url="https://example.edu"\nusername="u"\n[sync]\nroot="{root}"\n'
    path.write_text(text)

    def fail(_fd):
        raise OSError(error, "simulated storage failure")

    monkeypatch.setattr(fsutil.os, "fsync", fail)
    with pytest.raises(OSError):
        config_edit.set_value(path, ("sync", "notify"), False)
    assert path.read_text() == text
    assert not list(tmp_path.glob(".*.tmp"))


def test_scheduler_pins_paths_without_password(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIP_ROUTER_CONFIG", str(tmp_path / "space % $" / "c.toml"))
    monkeypatch.setenv("STUDIP_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "never-persist-this")
    plist = schedule.launchd_plist("/bin/studip", 60, "uni")
    service, _ = schedule.systemd_units("/bin/studip", 60, "uni")
    assert plist["EnvironmentVariables"]["STUDIP_ROUTER_CONFIG"].endswith("c.toml")
    assert "STUDIP_ROUTER_STATE_DIR=" in service
    assert "never-persist-this" not in repr(plist) + service
    assert "%%" in service and " $/c.toml" in service and "$$" not in service


def test_move_unsupported_hardlinks_fails_without_rename(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"source")

    def link(_src, _dst):
        dst.write_bytes(b"concurrent user file")
        raise OSError(errno.EOPNOTSUPP, "hard links unavailable")

    monkeypatch.setattr(fsutil.os, "link", link)
    with pytest.raises(OSError):
        move_no_clobber(src, dst)
    assert src.read_bytes() == b"source"
    assert dst.read_bytes() == b"concurrent user file"


def test_move_preserves_dangling_symlink(tmp_path):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_bytes(b"source")
    dst.symlink_to(tmp_path / "missing")
    with pytest.raises(FileExistsError):
        move_no_clobber(src, dst)
    assert dst.is_symlink() and src.read_bytes() == b"source"


def test_new_merge_does_not_replace_output_created_during_generation(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    source = tmp_path / "slides"
    source.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(source / "01.pdf")
    output = source / "merged.pdf"
    original_write = PdfWriter.write

    def racing_write(self, stream):
        result = original_write(self, stream)
        output.write_bytes(b"user content")
        return result

    monkeypatch.setattr(PdfWriter, "write", racing_write)
    result, _, _ = merge_pdfs(source, output, previous_sha=None, previous_fingerprint=None)
    assert not result.written and "changed" in result.reason
    assert output.read_bytes() == b"user content"
    assert not list(source.glob("*.part"))


def test_existing_merge_keeps_edits_during_generation(tmp_path, monkeypatch):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(tmp_path / "01.pdf")
    output = tmp_path / "merged.pdf"
    result, sha, _fingerprint = merge_pdfs(
        tmp_path, output, previous_sha=None, previous_fingerprint=None
    )
    assert result.written
    original_write = PdfWriter.write

    def racing_write(self, stream):
        result = original_write(self, stream)
        output.write_bytes(b"notes added during merge")
        return result

    monkeypatch.setattr(PdfWriter, "write", racing_write)
    result, _, _ = merge_pdfs(tmp_path, output, previous_sha=sha, previous_fingerprint="changed")
    assert not result.written and "changed" in result.reason
    assert output.read_bytes() == b"notes added during merge"


def test_path_limits_include_total_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(
        fsutil.os, "pathconf", lambda _p, name: 120 if name == "PC_PATH_MAX" else 255
    )
    assert path_problem(tmp_path / ("a" * 100) / "x.pdf")
    assert path_problem(Path("/tmp/x.pdf")) is None


def test_download_fsync_failure_is_recoverable(cfg, state, root, monkeypatch):
    file = remote("new.pdf")
    course = cfg.courses[COURSE_ID]
    plan = plan_course(cfg, course, [file], {})
    original_fsync = fsutil.os.fsync

    def fail(_fd):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(fsutil.os, "fsync", fail)
    failed = execute(plan, state, lambda _id, sink: sink(b"abc"), 1)
    assert len(failed.failed) == 1
    assert not (root / "Example Course" / "Sonstiges" / "new.pdf").exists()
    monkeypatch.setattr(fsutil.os, "fsync", original_fsync)
    again = plan_course(cfg, course, [file], state.for_course(COURSE_ID))
    done = execute(again, state, lambda _id, sink: sink(b"abc"), 2)
    assert len(done.done) == 1


def test_dst_change_and_overnight_move():
    berlin = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0)
    date = CourseDate("d", "c", "Course", start, start + timedelta(hours=1), "Exam", "", False)
    # Compare actual instants, including the repeated DST hour and UTC representation.
    same = replace(date, start=date.start.astimezone(UTC), end=date.end.astimezone(UTC))
    assert not diff({"d": date}, [same], datetime(2026, 10, 1, tzinfo=UTC))
    shifted = replace(date, start=datetime(2026, 10, 26, 0, 30, tzinfo=berlin))
    assert any(
        c.kind is ChangeKind.MOVED
        for c in diff({"d": date}, [shifted], datetime(2026, 10, 1, tzinfo=UTC))
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.edu",
        "https://example.edu:invalid",
        "https://[broken",
        "https://example.edu?secret=value",
    ],
)
def test_unsafe_server_addresses_are_rejected(root, url):
    with pytest.raises(ConfigError, match=r"server\.base_url"):
        parse(
            {"server": {"base_url": url, "username": "u"}, "sync": {"root": str(root)}},
            root / "cfg",
        )


def test_symlink_target_escape_is_reported(root, tmp_path):
    cfg = make_config(root, default_target="escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "Example Course" / "escape").symlink_to(outside, target_is_directory=True)
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("a.pdf")], {})
    assert any("escapes" in action.reason for action in plan.actions)
    assert not list(outside.iterdir())


def test_oversized_json_is_stopped(monkeypatch):
    import httpx

    from studip_router import api

    monkeypatch.setattr(api, "MAX_JSON_BYTES", 32)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"{" + b" " * 100))
    with (
        api.StudipClient("https://example.edu", "u", "secret", transport=transport) as client,
        pytest.raises(api.ApiError, match="JSON response exceeds"),
    ):
        client.me()


def test_direct_foreign_api_requests_never_receive_credentials():
    import httpx

    from studip_router.api import ApiError, StudipClient

    calls = []
    transport = httpx.MockTransport(lambda request: calls.append(request) or httpx.Response(200))
    with (
        StudipClient("https://example.edu", "u", "secret", transport=transport) as client,
        pytest.raises(ApiError, match="outside the configured"),
    ):
        client._get_json("https://other.example/data")
    assert not calls


@pytest.mark.parametrize("existing", [False, True])
def test_merge_does_not_adopt_a_new_baseline_after_authorization(tmp_path, monkeypatch, existing):
    from pypdf import PdfWriter

    from studip_router import postprocess

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    input_path = tmp_path / "01.pdf"
    writer.write(input_path)
    output = tmp_path / "merged.pdf"
    sha = None
    if existing:
        _, sha, _ = merge_pdfs(tmp_path, output, previous_sha=None, previous_fingerprint=None)
    original_stat = postprocess.local_stat

    def edit_during_input_scan(path):
        result = original_stat(path)
        if path == input_path:
            output.write_bytes(b"user edit after authorization")
        return result

    monkeypatch.setattr(postprocess, "local_stat", edit_during_input_scan)
    result, _, _ = merge_pdfs(tmp_path, output, previous_sha=sha, previous_fingerprint="changed")
    assert not result.written and "changed" in result.reason
    assert output.read_bytes() == b"user edit after authorization"


@pytest.mark.parametrize("recover", [True, False])
def test_json_body_transport_errors_retry_and_use_api_error(recover):
    import httpx

    from studip_router.api import ApiError, StudipClient

    calls = []

    class BrokenBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"data":'
            raise httpx.ReadError("interrupted body")

    def respond(request):
        calls.append(request)
        if recover and len(calls) > 1:
            return httpx.Response(200, json={"data": {"id": "u"}})
        return httpx.Response(200, stream=BrokenBody())

    with StudipClient(
        "https://example.edu",
        "u",
        "secret",
        max_retries=1,
        transport=httpx.MockTransport(respond),
        sleep=lambda _: None,
    ) as client:
        if recover:
            assert client.me()["id"] == "u"
        else:
            with pytest.raises(ApiError, match="network error while reading JSON"):
                client.me()
    assert len(calls) == 2


def test_interrupted_reorganize_can_undo_completed_moves(cfg, state, root, monkeypatch):
    from studip_router import reorganize

    files = [remote("first.pdf"), remote("second.pdf")]
    course = cfg.courses[COURSE_ID]
    execute(plan_course(cfg, course, files, {}), state, lambda _id, sink: sink(b"abc"), 1)
    new_cfg = replace(cfg, default_target="New")
    (root / "Example Course" / "New").mkdir()
    moves = reorganize.plan_reorganize(new_cfg, state)
    original_move = reorganize.move_no_clobber
    calls = []

    def interrupted(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise KeyboardInterrupt
        original_move(src, dst)

    monkeypatch.setattr(reorganize, "move_no_clobber", interrupted)
    with pytest.raises(KeyboardInterrupt):
        reorganize.apply_reorganize(moves, state)
    run, steps = reorganize.plan_undo(state)
    assert run is not None
    assert reorganize.apply_undo(state, run, steps) == 1
    for file in files:
        assert (root / "Example Course" / "Sonstiges" / file.name).read_bytes() == b"abc"


def test_database_failure_after_publication_is_adopted_on_next_run(cfg, state, root, monkeypatch):
    file = remote("recovered.pdf")
    course = cfg.courses[COURSE_ID]
    original = state.upsert

    def broken(_record):
        raise OSError("database write interrupted")

    monkeypatch.setattr(state, "upsert", broken)
    calls = []

    def download(fid, sink):
        calls.append(fid)
        sink(b"abc")

    outcome = execute(plan_course(cfg, course, [file], {}), state, download, 1)
    assert outcome.failed
    assert (root / "Example Course" / "Sonstiges" / file.name).read_bytes() == b"abc"
    monkeypatch.setattr(state, "upsert", original)
    plan = plan_course(cfg, course, [file], {})
    outcome = execute(plan, state, download, 2)
    assert not outcome.failed and len(calls) == 1
    assert file.id in state.for_course(COURSE_ID)


def test_undo_recovers_move_after_publication_before_completion_journal(
    cfg, state, root, monkeypatch
):
    from studip_router import reorganize

    file = remote("crash.pdf")
    course = cfg.courses[COURSE_ID]
    execute(plan_course(cfg, course, [file], {}), state, lambda _id, sink: sink(b"abc"), 1)
    (root / "Example Course" / "New").mkdir()
    moves = reorganize.plan_reorganize(replace(cfg, default_target="New"), state)
    original_journal = state.journal

    def interrupted(run, op, dst, src=None):
        if op == "move":
            raise KeyboardInterrupt
        original_journal(run, op, dst, src=src)

    monkeypatch.setattr(state, "journal", interrupted)
    with pytest.raises(KeyboardInterrupt):
        reorganize.apply_reorganize(moves, state)
    monkeypatch.setattr(state, "journal", original_journal)
    run, steps = reorganize.plan_undo(state)
    assert run is not None and any(step.op == "move" and not step.problem for step in steps)
    assert reorganize.apply_undo(state, run, steps) == 1
    assert (root / "Example Course" / "Sonstiges" / file.name).read_bytes() == b"abc"


def test_undo_recovers_interruption_between_link_and_unlink(cfg, state, root, monkeypatch):
    from studip_router import reorganize

    file = remote("hardlink.pdf")
    course = cfg.courses[COURSE_ID]
    execute(plan_course(cfg, course, [file], {}), state, lambda _id, sink: sink(b"abc"), 1)
    (root / "Example Course" / "New").mkdir()
    moves = reorganize.plan_reorganize(replace(cfg, default_target="New"), state)

    def link_only(src, dst):
        os.link(src, dst)
        raise KeyboardInterrupt

    monkeypatch.setattr(reorganize, "move_no_clobber", link_only)
    with pytest.raises(KeyboardInterrupt):
        reorganize.apply_reorganize(moves, state)
    run, steps = reorganize.plan_undo(state)
    assert run is not None and any(step.op == "unlink-hardlink" for step in steps)
    assert reorganize.apply_undo(state, run, steps) == 1
    assert (root / "Example Course" / "Sonstiges" / file.name).read_bytes() == b"abc"
    assert not (root / "Example Course" / "New" / file.name).exists()


def test_overlong_course_component_is_a_planning_error(cfg):
    from studip_router.config import CourseConfig
    from studip_router.planner import Kind

    course = CourseConfig("long", target="a" * 300)
    plan = plan_course(cfg, course, [], {})
    assert len(plan.actions) == 1 and plan.actions[0].kind is Kind.ERROR
    assert "too long" in plan.actions[0].reason


def test_reorganize_refuses_target_symlink_outside_course(cfg, state, root, tmp_path):
    from studip_router.reorganize import plan_reorganize

    file = remote("moved.pdf")
    course = cfg.courses[COURSE_ID]
    execute(plan_course(cfg, course, [file], {}), state, lambda _id, sink: sink(b"abc"), 1)
    outside = tmp_path / "outside-reorganize"
    outside.mkdir()
    (root / "Example Course" / "New").symlink_to(outside, target_is_directory=True)
    moves = plan_reorganize(replace(cfg, default_target="New"), state)
    assert moves and "escapes" in moves[0].problem
    assert not list(outside.iterdir())
