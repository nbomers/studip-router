"""Awkward names, broken transfers and other real-world file problems."""

from __future__ import annotations

import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from conftest import COURSE_ID, FakeDownloads, remote

from studip_router.api import ApiError, StudipClient
from studip_router.executor import DownloadError, execute, fetch_verified
from studip_router.fsutil import MAX_NAME_BYTES, local_stat, sanitize_filename, walk_files
from studip_router.planner import Kind, plan_course


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kapitel 1: Einführung.pdf", "Kapitel 1 - Einführung.pdf"),
        ('Was ist "gut"? <Teil 2>.pdf', "Was ist _gut__ _Teil 2_.pdf"),
        ("Blatt 1/2.pdf", "Blatt 1-2.pdf"),
        (".hidden.pdf", "hidden.pdf"),
        ("Folien...", "Folien"),
        ("  Leerzeichen.pdf  ", "Leerzeichen.pdf"),
        ("Q&amp;A.pdf", "Q&A.pdf"),
        ("CON.txt", "_CON.txt"),
        ("tab\there.pdf", "tabhere.pdf"),
        ("", "unnamed"),
        ("📚 Notes.pdf", "📚 Notes.pdf"),
    ],
)
def test_sanitize(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_keeps_colon_when_not_portable() -> None:
    assert sanitize_filename("a: b.pdf", portable=False) == "a: b.pdf"


def test_missing_extension_from_mime() -> None:
    assert sanitize_filename("Blatt 1", mime_type="application/pdf") == "Blatt 1.pdf"
    assert sanitize_filename("README", mime_type="application/x-unknown") == "README"


def test_long_names_are_truncated_by_bytes_keeping_extension() -> None:
    name = sanitize_filename("Ü" * 300 + ".pdf")
    assert name.endswith(".pdf")
    assert len(name.encode()) <= MAX_NAME_BYTES
    name.encode().decode()  # no broken multibyte character


def test_long_name_still_gets_version_suffix(cfg, state, root: Path) -> None:
    from datetime import UTC, datetime

    long = "x" * 300 + ".pdf"
    from conftest import FakeDownloads as FD

    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote(long)], {})
    execute(plan, state, FD(), run_id=1)
    newer = remote(long, size=4, chdate=datetime(2026, 12, 1, tzinfo=UTC))
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [newer], state.for_course(COURSE_ID))
    out = execute(plan, state, FD({f"file-{long}": b"new!"}), run_id=2)
    assert out.failed == []
    assert plan.actions[0].dest.name.endswith("_v2.pdf")
    assert len(plan.actions[0].dest.name.encode()) <= 255


def _guarded(content_chunks, size, name="a.pdf", tmp: Path | None = None):
    file = remote(name, size=size)

    def download(_id, sink):
        for chunk in content_chunks:
            sink(chunk)

    return file, download


def test_html_error_page_is_rejected(tmp_path: Path) -> None:
    file, download = _guarded([b"  <!DOCTYPE html><html>Login</html>"], 35)
    with pytest.raises(DownloadError, match="HTML page"):
        fetch_verified(file, tmp_path / "a.pdf", download)
    assert list(tmp_path.iterdir()) == []


def test_html_file_itself_is_fine(tmp_path: Path) -> None:
    file, download = _guarded([b"<html>ok</html>"], 15, name="page.html")
    fetch_verified(file, tmp_path / "page.html", download)
    assert (tmp_path / "page.html").read_bytes() == b"<html>ok</html>"


def test_truncated_transfer_is_retried(tmp_path: Path) -> None:
    calls = []

    def download(_id, sink):
        calls.append(1)
        sink(b"abc" if len(calls) == 1 else b"abcdef")

    fetch_verified(remote("a.pdf", size=6), tmp_path / "a.pdf", download)
    assert len(calls) == 2 and (tmp_path / "a.pdf").read_bytes() == b"abcdef"


def test_wrong_size_metadata_accepted_when_consistent(tmp_path: Path) -> None:
    def download(_id, sink):
        sink(b"abcd")

    fetch_verified(remote("a.pdf", size=99), tmp_path / "a.pdf", download)
    assert (tmp_path / "a.pdf").read_bytes() == b"abcd"


def test_connection_drop_mid_transfer_retries_then_fails(tmp_path: Path) -> None:
    def download(_id, sink):
        sink(b"ab")
        raise httpx.ReadError("connection reset")

    with pytest.raises(ApiError, match="download failed"):
        fetch_verified(remote("a.pdf", size=6), tmp_path / "a.pdf", download)
    assert list(tmp_path.iterdir()) == []


def test_failed_file_does_not_stop_the_others(cfg, state, root: Path) -> None:
    def download(file_id, sink):
        if file_id == "file-bad.pdf":
            raise httpx.ReadError("boom")
        sink(b"abc")

    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("bad.pdf"), remote("good.pdf")], {})
    outcome = execute(plan, state, download, run_id=1)
    assert [a.file.name for a, _ in outcome.failed] == ["bad.pdf"]
    assert (root / "Example Course" / "Sonstiges" / "good.pdf").exists()
    # the failed one is retried next time
    plan = plan_course(
        cfg,
        cfg.courses[COURSE_ID],
        [remote("bad.pdf"), remote("good.pdf")],
        state.for_course(COURSE_ID),
    )
    assert [a.file.name for a in plan.actions] == ["bad.pdf"]


@respx.mock
def test_credentials_not_sent_on_cross_host_redirect() -> None:
    api = "https://studip.example.edu/jsonapi.php/v1"
    respx.get(f"{api}/file-refs/x/content").mock(
        return_value=httpx.Response(302, headers={"Location": "https://cdn.example.org/f"})
    )
    target = respx.get("https://cdn.example.org/f").mock(
        return_value=httpx.Response(200, content=b"data")
    )
    StudipClient("https://studip.example.edu", "u", "secret").download("x", lambda b: None)
    assert "authorization" not in target.calls[0].request.headers


def test_old_icloud_placeholder_counts_as_present(cfg, state, root: Path) -> None:
    folder = root / "Example Course" / "Uebungen"
    (folder / ".Blatt 1.pdf.icloud").write_bytes(b"plist")
    assert local_stat(folder / "Blatt 1.pdf").dataless
    assert folder / "Blatt 1.pdf" in list(walk_files(root))
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("Blatt 1.pdf", size=500)], {})
    assert [a.kind for a in plan.actions] == [Kind.ADOPT]


def test_zip_member_names_are_repaired() -> None:
    from studip_router.postprocess import member_name

    legacy = zipfile.ZipInfo("Übung.txt".encode().decode("cp437"))
    legacy.flag_bits = 0  # UTF-8 bytes without the UTF-8 flag
    assert member_name(legacy) == "Übung.txt"
    dos = zipfile.ZipInfo("Übung.txt".encode("cp437").decode("cp437"))
    dos.flag_bits = 0  # genuine cp437 stays as Python decoded it
    assert member_name(dos) == "Übung.txt"


def test_zero_byte_file(cfg, state, root: Path) -> None:
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("empty.txt", size=0)], {})
    outcome = execute(plan, state, FakeDownloads({"file-empty.txt": b""}), run_id=1)
    assert outcome.failed == []
    assert (root / "Example Course" / "Sonstiges" / "empty.txt").read_bytes() == b""
