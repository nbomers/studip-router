from __future__ import annotations

import httpx
import pytest
import respx

from studip_sync.api import AuthFailed, StudipClient

BASE = "https://studip.example.edu"
API = BASE + "/jsonapi.php/v1"


def _client() -> StudipClient:
    return StudipClient(BASE, "jdoe", "secret", sleep=lambda _s: None)


def _semester(id: str, title: str, start: str, end: str) -> dict:
    return {
        "type": "semesters",
        "id": id,
        "attributes": {"title": title, "start": start, "end": end},
    }


@respx.mock
def test_pagination_follows_total() -> None:
    page1 = {
        "data": [
            _semester(f"s{i}", f"S{i}", "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00")
            for i in range(100)
        ],
        "meta": {"page": {"offset": 0, "limit": 100, "total": 101}},
    }
    page2 = {
        "data": [
            _semester("s100", "S100", "2027-01-01T00:00:00+00:00", "2027-02-01T00:00:00+00:00")
        ],
        "meta": {"page": {"offset": 100, "limit": 100, "total": 101}},
    }
    route = respx.get(f"{API}/semesters").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    assert len(_client().semesters()) == 101
    assert route.call_count == 2
    assert route.calls[1].request.url.params["page[offset]"] == "100"


@respx.mock
def test_retries_on_503_then_succeeds() -> None:
    route = respx.get(f"{API}/users/me").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"data": {"id": "u1", "attributes": {}}}),
        ]
    )
    assert _client().me()["id"] == "u1"
    assert route.call_count == 2


@respx.mock
def test_401_raises_without_leaking_password() -> None:
    respx.get(f"{API}/users/me").mock(return_value=httpx.Response(401))
    with pytest.raises(AuthFailed) as info:
        _client().me()
    assert "secret" not in str(info.value)


@respx.mock
def test_files_resolve_folder_paths_and_semesters() -> None:
    respx.get(f"{API}/courses/c1/folders").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "root",
                        "attributes": {"name": "Course", "folder-type": "RootFolder"},
                        "relationships": {},
                    },
                    {
                        "id": "f1",
                        "attributes": {"name": "Übungen", "folder-type": "StandardFolder"},
                        "relationships": {"parent": {"data": {"type": "folders", "id": "root"}}},
                    },
                    {
                        "id": "f2",
                        "attributes": {"name": "Blatt 1", "folder-type": "StandardFolder"},
                        "relationships": {"parent": {"data": {"type": "folders", "id": "f1"}}},
                    },
                ],
                "meta": {"page": {"total": 3}},
            },
        )
    )
    respx.get(f"{API}/courses/c1/file-refs").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "fr1",
                        "attributes": {
                            "name": "blatt01.pdf",
                            "filesize": 10,
                            "mime-type": "application/pdf",
                            "chdate": "2026-10-01T10:00:00+02:00",
                            "is-downloadable": True,
                        },
                        "relationships": {"parent": {"data": {"type": "folders", "id": "f2"}}},
                    }
                ],
                "meta": {"page": {"total": 1}},
            },
        )
    )
    [file] = _client().files("c1")
    assert file.folder_path == "Übungen/Blatt 1"
    assert file.size == 10


@respx.mock
def test_download_streams_content() -> None:
    respx.get(f"{API}/file-refs/fr1/content").mock(
        return_value=httpx.Response(200, content=b"x" * 200_000)
    )
    chunks: list[bytes] = []
    _client().download("fr1", chunks.append)
    assert b"".join(chunks) == b"x" * 200_000
    assert len(chunks) > 1
