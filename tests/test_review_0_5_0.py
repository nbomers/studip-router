"""Regression tests for the review findings before 0.5.0 (unzip, merge, local edits, moves)."""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from pathlib import Path

import pytest
from conftest import COURSE_ID, FakeDownloads, remote
from pypdf import PdfWriter

from studip_router import executor, postprocess
from studip_router.config import parse
from studip_router.executor import execute
from studip_router.fsutil import local_stat, move_no_clobber
from studip_router.planner import is_locally_modified, plan_course
from studip_router.postprocess import PostprocessError, extract_zip, merge_pdfs
from studip_router.state import FileRecord


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _pdf(path: Path, pages: int = 1) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with path.open("wb") as fh:
        writer.write(fh)


# -- 1. a crafted ZIP never stops the sync --------------------------------------------


def test_deeply_nested_zip_is_refused_and_leaves_nothing(tmp_path: Path) -> None:
    archive = tmp_path / "deep.zip"
    archive.write_bytes(_zip({"a/" * 2000 + "x.txt": b"x"}))
    with pytest.raises(PostprocessError, match="nested too deeply"):
        extract_zip(archive)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["deep.zip"]


def test_folder_entries_count_towards_the_entry_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(postprocess, "MAX_MEMBERS", 5)
    archive = tmp_path / "dirs.zip"
    archive.write_bytes(_zip({f"d{i}/": b"" for i in range(10)} | {"f.txt": b"f"}))
    with pytest.raises(PostprocessError, match="too many entries"):
        extract_zip(archive)


def _cfg(root: Path):
    return parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root)},
            "courses": {COURSE_ID: {"target": "Example Course", "unzip": True}},
        },
        Path("c.toml"),
    )


def test_any_unzip_error_fails_only_that_file(
    root: Path, state, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg(root)
    data = _zip({"a.pdf": b"pdf"})
    files = [remote("A.zip", size=len(data)), remote("B.zip", size=len(data))]
    plan = plan_course(cfg, cfg.courses[COURSE_ID], files, {})
    real = executor.extract_zip

    def flaky(path: Path):
        if path.name == "A.zip":  # e.g. RecursionError, or RuntimeError for encrypted
            raise RuntimeError("File is encrypted, password required for extraction")
        return real(path)

    monkeypatch.setattr(executor, "extract_zip", flaky)
    content = {"file-A.zip": data, "file-B.zip": data}
    outcome = execute(plan, state, FakeDownloads(content), run_id=1)
    assert [a.file.name for a, _ in outcome.failed] == ["A.zip"]
    assert "encrypted" in outcome.failed[0][1]
    assert [p.name for p in outcome.extracted] == ["B"]


# -- 2. merging never replaces an output saved meanwhile ------------------------------


def _merged(tmp_path: Path) -> tuple[Path, str, str]:
    _pdf(tmp_path / "a.pdf")
    out = tmp_path / "All.pdf"
    result, sha, fp = merge_pdfs(tmp_path, out, previous_sha=None, previous_fingerprint=None)
    assert result.written and sha and fp
    return out, sha, fp


def test_merge_keeps_an_output_edited_while_merging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sha, fp = _merged(tmp_path)
    _pdf(tmp_path / "b.pdf")  # new input: a merge is due
    real_write = PdfWriter.write

    def write_and_edit(self, stream):
        out.write_bytes(b"my notes")  # saved by the user while merging
        return real_write(self, stream)

    monkeypatch.setattr(PdfWriter, "write", write_and_edit)
    result, new_sha, new_fp = merge_pdfs(tmp_path, out, previous_sha=sha, previous_fingerprint=fp)
    assert not result.written and "changed while merging" in result.reason
    assert out.read_bytes() == b"my notes"
    assert (new_sha, new_fp) == (sha, fp)
    assert not list(tmp_path.glob("*.part"))


def test_merge_never_replaces_a_new_output_that_appeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pdf(tmp_path / "a.pdf")
    out = tmp_path / "All.pdf"
    real_write = PdfWriter.write

    def write_and_create(self, stream):
        out.write_bytes(b"someone else's file")
        return real_write(self, stream)

    monkeypatch.setattr(PdfWriter, "write", write_and_create)
    result, sha, _ = merge_pdfs(tmp_path, out, previous_sha=None, previous_fingerprint=None)
    assert not result.written and sha is None
    assert out.read_bytes() == b"someone else's file"


# -- 3. an edit with the same size and time is noticed before a replace ---------------


def test_same_size_and_time_edit_is_found_before_a_replace(tmp_path: Path) -> None:
    path = tmp_path / "Blatt.pdf"
    path.write_bytes(b"original")
    st = local_stat(path)
    assert st is not None
    record = FileRecord(
        "f1", COURSE_ID, "Blatt.pdf", "", "2026-10-01T12:00:00+00:00", 8,
        str(path), st.size, st.mtime, hashlib.sha256(b"original").hexdigest(),
    )  # fmt: skip
    path.write_bytes(b"annotat!")  # same size
    os.utime(path, (st.mtime, st.mtime))  # and the old time
    st = local_stat(path)
    assert st is not None
    assert not is_locally_modified(record, st, path)  # the cheap check misses it
    assert is_locally_modified(record, st, path, thorough=True)


# -- 4. no-clobber moves without hard links -------------------------------------------


def _no_links(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args, **_kwargs):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", refuse)


def test_move_without_hard_links_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_links(monkeypatch)
    src, dst = tmp_path / "a.part", tmp_path / "a.pdf"
    src.write_bytes(b"new")
    move_no_clobber(src, dst)
    assert dst.read_bytes() == b"new" and not src.exists()


def test_move_without_hard_links_never_replaces_a_file_that_appeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_links(monkeypatch)
    src, dst = tmp_path / "a.part", tmp_path / "a.pdf"
    src.write_bytes(b"new")
    real_exists = Path.exists
    calls = {"n": 0}

    def exists_then_appears(self: Path) -> bool:
        if self == dst and calls["n"] == 0:
            calls["n"] += 1
            result = real_exists(self)
            dst.write_bytes(b"saved by the user")  # right after the check
            return result
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", exists_then_appears)
    with pytest.raises(FileExistsError):
        move_no_clobber(src, dst)
    assert dst.read_bytes() == b"saved by the user" and src.read_bytes() == b"new"
