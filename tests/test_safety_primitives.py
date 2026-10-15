"""The small functions every safety promise rests on (gaps found by mutation testing)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest
import respx
from fake_studip import API, BASE, COURSE, install
from typer.testing import CliRunner

from studip_router import paths
from studip_router.cli import app
from studip_router.fsutil import LocalStat, move_no_clobber
from studip_router.planner import is_locally_modified
from studip_router.state import FileRecord


def _record(path: Path, content: bytes) -> FileRecord:
    import hashlib

    st = path.stat()
    return FileRecord(
        file_id="f",
        course_id="c",
        remote_name=path.name,
        remote_folder_path="",
        chdate="2026-10-01T00:00:00+00:00",
        filesize=len(content),
        local_path=str(path),
        local_size=st.st_size,
        local_mtime=st.st_mtime,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def test_an_edit_of_the_same_size_is_noticed(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"original")
    record = _record(path, b"original")
    path.write_bytes(b"ORIGINAL")  # same size, different content
    os.utime(path, (record.local_mtime + 5, record.local_mtime + 5))
    st = path.stat()
    assert is_locally_modified(record, LocalStat(st.st_size, st.st_mtime, False), path)


def test_untouched_file_is_not_modified(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"original")
    record = _record(path, b"original")
    os.utime(path, (record.local_mtime + 5, record.local_mtime + 5))  # touched, same bytes
    st = path.stat()
    assert not is_locally_modified(record, LocalStat(st.st_size, st.st_mtime, False), path)


def test_an_icloud_only_file_counts_as_modified(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"original")
    record = _record(path, b"original")
    evicted = LocalStat(record.local_size, record.local_mtime + 60, dataless=True)
    assert is_locally_modified(record, evicted, path)  # never downloaded to compare


def test_move_never_replaces_an_existing_file(tmp_path: Path) -> None:
    src, dst = tmp_path / "a.pdf", tmp_path / "b.pdf"
    src.write_bytes(b"mine")
    dst.write_bytes(b"also mine")
    with pytest.raises(FileExistsError):
        move_no_clobber(src, dst)
    assert src.read_bytes() == b"mine" and dst.read_bytes() == b"also mine"


@respx.mock
def test_a_rejected_login_pauses_auto_sync(root: Path, monkeypatch) -> None:
    cfg = Path(paths.config_file())
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"[server]\nbase_url = '{BASE}'\nusername = 'jdoe'\n[sync]\nroot = '{root}'\n"
        f"[courses.{COURSE}]\nname = 'Q'\ntarget = 'Example Course'\n"
    )
    monkeypatch.setenv("STUDIP_ROUTER_PASSWORD", "wrong")
    install(respx.mock)
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(401))
    result = CliRunner().invoke(app, ["sync", "--notify", "--quiet"])
    assert result.exit_code == 3
    assert paths.auth_pause_file().exists()
    calls = respx.calls.call_count
    again = CliRunner().invoke(app, ["sync", "--notify", "--quiet"])
    assert again.exit_code == 3 and respx.calls.call_count == calls  # no new login attempt
