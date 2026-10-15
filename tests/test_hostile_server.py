"""A broken or hostile server must not exhaust disk, memory or time."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from conftest import COURSE_ID, make_config, remote

from studip_router.api import ApiError, StudipClient
from studip_router.executor import TooLargeError, execute, fetch_verified
from studip_router.fsutil import sanitize_filename
from studip_router.planner import plan_course

BASE = "https://studip.example.edu"
API = BASE + "/jsonapi.php/v1"


def _sem(i: int) -> dict:
    return {
        "type": "semesters",
        "id": f"s{i}",
        "attributes": {
            "title": f"S{i}",
            "start": "2026-01-01T00:00:00+01:00",
            "end": "2026-06-30T00:00:00+02:00",
        },
    }


@respx.mock
def test_pagination_without_end_is_stopped(monkeypatch) -> None:
    monkeypatch.setattr("studip_router.api.MAX_PAGES", 5)
    calls = iter(range(10_000))

    def page(request: httpx.Request) -> httpx.Response:
        start = next(calls) * 100
        return httpx.Response(200, json={"data": [_sem(start + i) for i in range(100)]})

    respx.get(f"{API}/semesters").mock(side_effect=page)
    with pytest.raises(ApiError, match="more than 5 pages"):
        StudipClient(BASE, "u", "p").semesters()


@respx.mock
def test_a_server_ignoring_the_offset_yields_each_record_once() -> None:
    same = {"data": [_sem(i) for i in range(100)], "meta": {"page": {"total": 1000}}}
    route = respx.get(f"{API}/semesters").mock(return_value=httpx.Response(200, json=same))
    client = StudipClient(BASE, "u", "p")
    assert len(client.semesters()) == 100
    assert route.call_count == 2  # the repeated page stops the loop
    assert any("repeated a page" in w for w in client.warnings)


def test_far_more_than_announced_is_stopped_early(tmp_path: Path) -> None:
    sent = {"bytes": 0}

    def endless(_fid, sink):
        for _ in range(200):
            sent["bytes"] += 1 << 20
            sink(b"\0" * (1 << 20))

    with pytest.raises(TooLargeError):
        fetch_verified(remote("x.pdf", size=1024), tmp_path / "x.pdf", endless)
    assert sent["bytes"] <= 2 << 20  # stopped after about 1 MB, not 200, and not retried
    assert not list(tmp_path.iterdir())


def test_a_slightly_wrong_size_is_still_accepted(tmp_path: Path) -> None:
    _sha, written = fetch_verified(
        remote("x.pdf", size=1000), tmp_path / "x.pdf", lambda _f, sink: sink(b"y" * 1100)
    )
    assert written and (tmp_path / "x.pdf").stat().st_size == 1100


def test_the_size_limit_applies_while_downloading(cfg, state, root: Path) -> None:
    cfg = make_config(root, exclude={"max_size_mb": 1})
    unknown_size = remote("big.bin", size=0)  # announced as 0 bytes: unknown
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [unknown_size], {})
    assert plan.max_bytes == 1 << 20
    outcome = execute(plan, state, lambda _f, sink: sink(b"z" * (3 << 20)), run_id=1)
    assert len(outcome.failed) == 1 and "more than 1 MB" in outcome.failed[0][1]
    assert not list((root / "Example Course" / "Sonstiges").iterdir())


def test_include_large_lifts_the_limit(cfg, root: Path) -> None:
    cfg = make_config(root, exclude={"max_size_mb": 1})
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("a.pdf")], {}, include_large=True)
    assert plan.max_bytes is None


@respx.mock
def test_redirects_to_plain_http_are_refused() -> None:
    respx.get(f"{API}/file-refs/x/content").mock(
        return_value=httpx.Response(302, headers={"Location": "http://cdn.example.org/f"})
    )
    plain = respx.get("http://cdn.example.org/f").mock(return_value=httpx.Response(200))
    with pytest.raises(ApiError, match=r"redirect to http://cdn\.example\.org"):
        StudipClient(BASE, "u", "secret").download("x", lambda b: None)
    assert not plain.called


@respx.mock
def test_redirects_to_other_https_hosts_still_work() -> None:
    respx.get(f"{API}/file-refs/x/content").mock(
        return_value=httpx.Response(302, headers={"Location": "https://cdn.example.org/f"})
    )
    respx.get("https://cdn.example.org/f").mock(return_value=httpx.Response(200, content=b"ok"))
    got: list[bytes] = []
    StudipClient(BASE, "u", "secret").download("x", got.append)
    assert b"".join(got) == b"ok"


def test_bidi_controls_are_removed_from_file_names() -> None:
    assert sanitize_filename("Loesung‮fdp.exe") == "Loesungfdp.exe"
    assert sanitize_filename("a⁦b⁩c‏.pdf") == "abc.pdf"
