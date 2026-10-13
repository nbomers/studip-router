from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from conftest import COURSE_ID, FakeDownloads, remote
from pypdf import PdfReader, PdfWriter

from studip_router.config import parse
from studip_router.executor import execute
from studip_router.planner import plan_course
from studip_router.postprocess import PostprocessError, extract_zip, merge_inputs, merge_pdfs


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with path.open("wb") as fh:
        writer.write(fh)


def test_extract_zip_next_to_archive(tmp_path: Path) -> None:
    archive = tmp_path / "1 Markov.zip"
    archive.write_bytes(_zip({"slides/a.pdf": b"a", "__MACOSX/._a.pdf": b"x", ".DS_Store": b""}))
    folder = extract_zip(archive)
    assert folder == tmp_path / "1 Markov"
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == [
        "slides/a.pdf"
    ]
    assert archive.exists()  # original is kept


def test_extract_never_touches_existing_folder(tmp_path: Path) -> None:
    archive = tmp_path / "x.zip"
    archive.write_bytes(_zip({"a.txt": b"new"}))
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "a.txt").write_text("mine")
    assert extract_zip(archive) is None
    assert (tmp_path / "x" / "a.txt").read_text() == "mine"


def test_zip_slip_is_rejected_and_leaves_nothing(tmp_path: Path) -> None:
    archive = tmp_path / "evil.zip"
    archive.write_bytes(_zip({"ok.txt": b"1", "../escape.txt": b"2"}))
    with pytest.raises(PostprocessError, match="unsafe path"):
        extract_zip(archive)
    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "evil").exists()
    assert not (tmp_path / "evil.unzip-tmp").exists()


def test_sync_unzips_when_enabled(root: Path, state) -> None:
    cfg = parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root)},
            "courses": {COURSE_ID: {"target": "Example Course", "unzip": True}},
        },
        Path("c.toml"),
    )
    data = _zip({"lecture.pdf": b"pdf"})
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("0 Intro.zip", size=len(data))], {})
    assert plan.actions[0].unzips
    outcome = execute(plan, state, FakeDownloads({"file-0 Intro.zip": data}), run_id=1)
    folder = root / "Example Course" / "Sonstiges" / "00 Intro"
    assert outcome.extracted == [folder]
    assert (folder / "lecture.pdf").read_bytes() == b"pdf"


def test_merge_inputs_natural_order_newest_version(tmp_path: Path) -> None:
    for name in [
        "lecture-10.pdf",
        "lecture-2.pdf",
        "lecture-1.pdf",
        "lecture-1_v2.pdf",
        "notes.txt",
        "All.pdf",
    ]:
        (tmp_path / name).write_bytes(b"x")
    names = [p.name for p in merge_inputs(tmp_path, "All.pdf")]
    assert names == ["lecture-1_v2.pdf", "lecture-2.pdf", "lecture-10.pdf"]


def test_merge_pdfs_and_protection(tmp_path: Path) -> None:
    _pdf(tmp_path / "a.pdf", 2)
    _pdf(tmp_path / "b.pdf", 3)
    out = tmp_path / "All.pdf"
    result, sha, fp = merge_pdfs(tmp_path, out, previous_sha=None, previous_fingerprint=None)
    assert result.written and len(PdfReader(out).pages) == 5

    again, *_ = merge_pdfs(tmp_path, out, previous_sha=sha, previous_fingerprint=fp)
    assert not again.written and again.reason == "up to date"

    _pdf(tmp_path / "c.pdf", 1)
    out.write_bytes(b"edited by hand")
    edited, *_ = merge_pdfs(tmp_path, out, previous_sha=sha, previous_fingerprint=fp)
    assert not edited.written and "modified" in edited.reason
    assert out.read_bytes() == b"edited by hand"


def test_merge_refuses_foreign_output(tmp_path: Path) -> None:
    _pdf(tmp_path / "a.pdf", 1)
    (tmp_path / "All.pdf").write_bytes(b"someone else's")
    result, *_ = merge_pdfs(
        tmp_path, tmp_path / "All.pdf", previous_sha=None, previous_fingerprint=None
    )
    assert not result.written and "not created" in result.reason
