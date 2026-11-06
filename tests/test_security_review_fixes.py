"""Security boundaries and recovery paths exercised with synthetic data only."""

from __future__ import annotations

import errno
import io
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, make_config, remote
from fake_studip import API, BASE, COURSE, install
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend
from pypdf import PdfWriter
from typer.testing import CliRunner

from studip_router import auth, paths, postprocess
from studip_router.cli import app
from studip_router.executor import execute
from studip_router.fsutil import LocalStat, local_stat, move_no_clobber
from studip_router.planner import Kind, plan_course
from studip_router.postprocess import PostprocessError, extract_zip, merge_pdfs, unlock_pdf
from studip_router.reorganize import apply_undo, plan_undo
from studip_router.tomlwrite import append_blocks


def zip_bytes(entries: list[str]) -> bytes:
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in entries:
            archive.writestr(name, b"" if name.endswith("/") else b"content")
    return buffer.getvalue()


@pytest.mark.parametrize("directory", [False, True])
def test_archive_depth_is_bounded_before_creating_directories(tmp_path, directory):
    archive = tmp_path / "deep.zip"
    archive.write_bytes(zip_bytes(["a/" * 1100 + ("" if directory else "x.txt")]))
    with pytest.raises(PostprocessError, match="nested too deeply"):
        extract_zip(archive)
    assert list(tmp_path.iterdir()) == [archive]


def test_archive_count_includes_directory_records(tmp_path, monkeypatch):
    monkeypatch.setattr(postprocess, "MAX_MEMBERS", 2)
    archive = tmp_path / "entries.zip"
    archive.write_bytes(zip_bytes(["one/", "two/", "three/"]))
    with pytest.raises(PostprocessError, match="too many entries"):
        extract_zip(archive)
    archive.write_bytes(zip_bytes(["one/", "one/file.txt"]))
    assert (extract_zip(archive) / "one/file.txt").read_bytes() == b"content"


def test_invalid_archive_is_isolated_and_not_retried(root, state):
    cfg = make_config(root, unzip=True)
    data = zip_bytes(["a/" * 1100 + "x.txt"])
    archive = remote("deep.zip", size=len(data), mime="application/zip")
    later = remote("z.txt", mime="text/plain")
    downloads = FakeDownloads({archive.id: data, later.id: b"abc"})
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [archive, later], {})
    outcome = execute(plan, state, downloads, 1)
    assert len(outcome.failed) == 1
    assert (root / "Example Course/Sonstiges/z.txt").read_bytes() == b"abc"
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [archive, later], state.for_course(COURSE_ID))
    assert plan.actions == []
    downloads.calls.clear()
    retried = execute(plan, state, downloads, 2)
    assert not retried.failed and downloads.calls == []


@pytest.mark.parametrize("delta", [0, 0.5])
def test_overwrite_preserves_same_size_edits_despite_matching_timestamp(root, state, delta):
    cfg = make_config(root, on_update="overwrite")
    first = remote("notes.pdf", size=3)
    course = cfg.courses[COURSE_ID]
    execute(plan_course(cfg, course, [first], {}), state, FakeDownloads({first.id: b"old"}), 1)
    record = state.get(first.id)
    path = Path(record.local_path)
    path.write_bytes(b"NEW")
    os.utime(path, (record.local_mtime + delta, record.local_mtime + delta))
    changed = replace(first, chdate=datetime(2026, 11, 1, tzinfo=UTC))
    plan = plan_course(cfg, course, [changed], state.for_course(COURSE_ID))
    assert [a.kind for a in plan.actions] == [Kind.NEW_VERSION]


def test_overwrite_rechecks_same_metadata_during_download(root, state):
    cfg = make_config(root, on_update="overwrite")
    first = remote("notes.pdf", size=3)
    course = cfg.courses[COURSE_ID]
    execute(plan_course(cfg, course, [first], {}), state, FakeDownloads({first.id: b"old"}), 1)
    record = state.get(first.id)
    path = Path(record.local_path)
    changed = replace(first, chdate=datetime(2026, 11, 1, tzinfo=UTC))
    plan = plan_course(cfg, course, [changed], state.for_course(COURSE_ID))

    def download(_id, sink):
        path.write_bytes(b"MY!")
        os.utime(path, (record.local_mtime, record.local_mtime))
        sink(b"new")

    result = execute(plan, state, download, 2)
    assert result.failed and path.read_bytes() == b"MY!"


@pytest.mark.parametrize("appears", [False, True])
def test_no_hardlink_fallback_never_replaces_an_intervening_file(tmp_path, monkeypatch, appears):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"download")

    def no_links(*args):
        if appears:
            target.write_bytes(b"user data")
        raise OSError(errno.EOPNOTSUPP, "no hard links")

    monkeypatch.setattr("studip_router.fsutil.os.link", no_links)
    if appears:
        with pytest.raises(FileExistsError):
            move_no_clobber(source, target)
        assert source.read_bytes() == b"download" and target.read_bytes() == b"user data"
    else:
        move_no_clobber(source, target)
        assert not source.exists() and target.read_bytes() == b"download"


def pdf_file(path):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)


@pytest.mark.parametrize("existing", [False, True])
def test_merge_keeps_files_saved_during_pdf_processing(tmp_path, existing):
    pdf_file(tmp_path / "a.pdf")
    output = tmp_path / "All.pdf"
    sha = fingerprint = None
    if existing:
        _, sha, fingerprint = merge_pdfs(
            tmp_path, output, previous_sha=None, previous_fingerprint=None
        )
        pdf_file(tmp_path / "b.pdf")
    original = PdfWriter.append

    def edit_during_append(self, *args, **kwargs):
        output.write_bytes(b"my new annotations")
        return original(self, *args, **kwargs)

    with patch.object(PdfWriter, "append", edit_during_append):
        result, _, _ = merge_pdfs(
            tmp_path, output, previous_sha=sha, previous_fingerprint=fingerprint
        )
    assert not result.written and "changed while merging" in result.reason
    assert output.read_bytes() == b"my new annotations"
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("legacy_placeholder", [False, True])
def test_merge_preserves_unverifiable_cloud_output(tmp_path, monkeypatch, legacy_placeholder):
    pdf_file(tmp_path / "a.pdf")
    output = tmp_path / "All.pdf"
    _, sha, fingerprint = merge_pdfs(tmp_path, output, previous_sha=None, previous_fingerprint=None)
    pdf_file(tmp_path / "b.pdf")
    if legacy_placeholder:
        output.unlink()
        (tmp_path / ".All.pdf.icloud").write_bytes(b"placeholder")
    else:
        output.write_bytes(b"cloud annotations")

        def cloud_stat(path):
            st = local_stat(path)
            return LocalStat(st.size, st.mtime, True) if path == output else st

        monkeypatch.setattr(postprocess, "local_stat", cloud_stat)
    result, _, _ = merge_pdfs(
        tmp_path,
        output,
        previous_sha=sha,
        previous_fingerprint=fingerprint,
        allow_icloud_download=True,
    )
    assert not result.written and "output only in iCloud" in result.reason
    if not legacy_placeholder:
        assert output.read_bytes() == b"cloud annotations"


def test_unlock_keeps_an_edit_made_while_processing(tmp_path):
    output = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("dummy-course-password")
    writer.write(output)
    original = PdfWriter.write

    def edit_during_write(self, *args, **kwargs):
        output.write_bytes(b"my annotations")
        return original(self, *args, **kwargs)

    with patch.object(PdfWriter, "write", edit_during_write), pytest.raises(PostprocessError):
        unlock_pdf(output, "dummy-course-password")
    assert output.read_bytes() == b"my annotations"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.items = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[service, username] = password

    def delete_password(self, service, username):
        import keyring.errors

        if (service, username) not in self.items:
            raise keyring.errors.PasswordDeleteError()
        del self.items[service, username]


def test_unknown_file_keyring_is_rejected_for_all_passwords(monkeypatch):
    backend = MemoryKeyring()  # even positive-priority, non-OS backends are refused
    monkeypatch.setattr("keyring.get_keyring", lambda: backend)
    monkeypatch.delenv("STUDIP_ROUTER_PASSWORD", raising=False)
    assert auth.keychain_problem()
    for call in (
        lambda: auth.set_password(BASE, "dummy-user", "dummy-password"),
        lambda: auth.get_password(BASE, "dummy-user"),
        lambda: auth.delete_password(BASE, "dummy-user"),
        lambda: auth.set_pdf_password("course", "dummy-password", base_url=BASE, username="u"),
        lambda: auth.delete_pdf_password("course", base_url=BASE, username="u"),
    ):
        with pytest.raises(auth.AuthError, match="OS keychain"):
            call()
    assert not backend.items
    assert not auth.has_password(BASE, "dummy-user")
    assert auth.get_pdf_password("course", base_url=BASE, username="u") is None
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "headless-dummy")
    assert auth.get_password(BASE, "dummy-user") == "headless-dummy"


def test_chainer_uses_approved_os_backend_without_file_fallback(monkeypatch):
    approved_type = type(
        "Keyring", (MemoryKeyring,), {"__module__": "keyring.backends.SecretService"}
    )
    approved, unsafe = approved_type(), MemoryKeyring()

    class Chain(ChainerBackend):
        backends: ClassVar = [unsafe, approved]

    monkeypatch.setattr("keyring.get_keyring", Chain)
    auth.set_password(BASE, "u", "dummy-secret")
    assert approved.items and not unsafe.items
    assert auth.get_password(BASE, "u") == "dummy-secret"


def test_pdf_passwords_are_bound_to_server_account_and_profile(monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(auth, "_os_keyring", lambda: backend)
    backend.items[auth.SERVICE, "pdf:course"] = "ambiguous-legacy-password"
    scope = {"base_url": BASE, "username": "u"}
    try:
        paths.set_profile("one")
        assert auth.get_pdf_password("course", **scope) is None
        auth.set_pdf_password("course", "dummy-pdf-password", **scope)
        for changed in (
            {**scope, "username": "other"},
            {**scope, "base_url": "https://other.example.edu"},
            {**scope, "base_url": BASE + ":8443"},
            {**scope, "base_url": BASE + "/other-installation"},
        ):
            assert auth.get_pdf_password("course", **changed) is None
        paths.set_profile("two")
        assert auth.get_pdf_password("course", **scope) is None
        assert not auth.delete_pdf_password("course", **scope)
        paths.set_profile("one")
        assert auth.get_pdf_password("course", **scope) == "dummy-pdf-password"
        assert auth.delete_pdf_password("course", **scope)
        assert backend.items[auth.SERVICE, "pdf:course"] == "ambiguous-legacy-password"
    finally:
        paths.set_profile(None)


@pytest.mark.parametrize("endpoint", ["news", "events"])
@respx.mock
def test_rejected_auth_in_extra_checks_pauses_background_sync(root, monkeypatch, endpoint):
    config = paths.config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        f"[server]\nbase_url='{BASE}'\nusername='u'\n[sync]\nroot='{root}'\n"
        f"[courses.{COURSE}]\ntarget='Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "synthetic-only")
    monkeypatch.setattr("studip_router.cli.notify", lambda *args: True)
    install(respx.mock)
    respx.get(f"{API}/courses/{COURSE}/{endpoint}").respond(401)
    runner = CliRunner()
    first = runner.invoke(app, ["--json", "sync", "--notify", "--quiet"])
    assert first.exit_code == 3 and paths.auth_pause_file().exists()
    assert "synthetic-only" not in first.output
    calls = respx.calls.call_count
    assert runner.invoke(app, ["sync", "--notify", "--quiet"]).exit_code == 3
    assert respx.calls.call_count == calls
    assert app.pretty_exceptions_show_locals is False


def test_undo_retries_only_unfinished_steps(tmp_path, state):
    original = [tmp_path / "old-one.pdf", tmp_path / "old-two.pdf"]
    moved = [tmp_path / "new-one.pdf", tmp_path / "new-two.pdf"]
    run = state.start_run("reorganize")
    for old, new in zip(original, moved, strict=True):
        new.write_bytes(b"synced")
        state.journal(run, "move", str(new), str(old))
    state.finish_run(run, {"moved": 2})
    original[0].write_bytes(b"occupied")
    run_id, steps = plan_undo(state)
    assert apply_undo(state, run_id, steps) == 1
    original[0].unlink()
    run_id, steps = plan_undo(state)
    assert len(steps) == 1 and not steps[0].problem
    assert apply_undo(state, run_id, steps) == 1
    assert plan_undo(state) == (None, [])
    assert all(path.read_bytes() == b"synced" for path in original)


def test_rule_append_keeps_private_permissions(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[server]\nbase_url='https://example.edu'\nusername='dummy-user'\n"
        f"[sync]\nroot='{tmp_path / 'uni'}'\n"
    )
    config.chmod(0o600)
    previous_umask = os.umask(0o022)
    try:
        append_blocks(config, [], "synthetic review")
    finally:
        os.umask(previous_umask)
    assert config.stat().st_mode & 0o777 == 0o600
    assert config.with_suffix(".toml.bak").stat().st_mode & 0o777 == 0o600


def test_undo_keeps_progress_if_a_later_move_fails(tmp_path, state, monkeypatch):
    original = [tmp_path / "old-one.pdf", tmp_path / "old-two.pdf"]
    moved = [tmp_path / "new-one.pdf", tmp_path / "new-two.pdf"]
    run = state.start_run("reorganize")
    for old, new in zip(original, moved, strict=True):
        new.write_bytes(b"synced")
        state.journal(run, "move", str(new), str(old))
    state.finish_run(run, {"moved": 2})
    real_move = move_no_clobber

    def fail_second(source, dest):
        if source == moved[0]:
            raise OSError("simulated disk error")
        real_move(source, dest)

    run_id, steps = plan_undo(state)
    with monkeypatch.context() as patcher:
        patcher.setattr("studip_router.reorganize.move_no_clobber", fail_second)
        with pytest.raises(OSError, match="disk error"):
            apply_undo(state, run_id, steps)
    run_id, steps = plan_undo(state)
    assert len(steps) == 1 and steps[0].dst == str(moved[0])
    assert not steps[0].problem
    assert apply_undo(state, run_id, steps) == 1
    assert plan_undo(state) == (None, [])
