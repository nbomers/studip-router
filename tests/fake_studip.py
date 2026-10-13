"""A tiny fake Stud.IP JSON:API (respx) with invented data for end-to-end tests."""

from __future__ import annotations

import httpx
import respx

BASE = "https://studip.example.edu"
API = BASE + "/jsonapi.php/v1"
COURSE = "c0ffee00c0ffee00c0ffee00c0ffee00"
OTHER = "0de11ca7e550de11ca7e550de11ca7e5"

FILES = {
    "f-slides1": ("lecture-1.pdf", "folder-slides", b"%PDF-slides-one"),
    "f-sheet3": ("Blatt 3.pdf", "folder-sheets", b"%PDF-sheet-three"),
    "f-misc": ("Orga.pdf", "root", b"%PDF-orga"),
}


def _page(data: list, included: list | None = None) -> dict:
    doc = {"data": data, "meta": {"page": {"offset": 0, "limit": 100, "total": len(data)}}}
    if included:
        doc["included"] = included
    return doc


def install(router: respx.MockRouter) -> None:
    sem = {
        "type": "semesters",
        "id": "sem-now",
        "attributes": {
            "title": "WiSe 2026/27",
            "start": "2026-10-01T00:00:00+02:00",
            "end": "2027-03-31T23:59:59+02:00",
        },
    }
    old = {
        "type": "semesters",
        "id": "sem-old",
        "attributes": {
            "title": "SoSe 2026",
            "start": "2026-04-01T00:00:00+02:00",
            "end": "2026-09-30T23:59:59+02:00",
        },
    }
    router.get(f"{API}/discovery").mock(return_value=httpx.Response(200, json={"data": []}))
    router.get(f"{API}/semesters").mock(return_value=httpx.Response(200, json=_page([old, sem])))
    router.get(f"{API}/users/me").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "u1", "attributes": {"formatted-name": "Jane Doe"}}}
        )
    )

    def course(cid: str, title: str, sem_id: str) -> dict:
        return {
            "type": "courses",
            "id": cid,
            "attributes": {"title": title},
            "relationships": {
                "start-semester": {"data": {"type": "semesters", "id": sem_id}},
                "end-semester": {"data": {"type": "semesters", "id": sem_id}},
            },
        }

    router.get(f"{API}/users/u1/courses").mock(
        return_value=httpx.Response(
            200,
            json=_page(
                [
                    course(COURSE, "Introduction to Quantum Cooking", "sem-now"),
                    course(OTHER, "Underwater Basket Weaving", "sem-now"),
                    course("01d01d01d01d01d01d01d01d01d01d01", "Old Course", "sem-old"),
                ]
            ),
        )
    )

    def folder(fid: str, name: str, parent: str | None) -> dict:
        rel = {"parent": {"data": {"type": "folders", "id": parent}}} if parent else {}
        return {
            "type": "folders",
            "id": fid,
            "attributes": {
                "name": name,
                "folder-type": "RootFolder" if parent is None else "StandardFolder",
            },
            "relationships": rel,
        }

    router.get(f"{API}/courses/{COURSE}/folders").mock(
        return_value=httpx.Response(
            200,
            json=_page(
                [
                    folder("root", "Course", None),
                    folder("folder-slides", "Folien", "root"),
                    folder("folder-sheets", "Übungsblätter", "root"),
                ]
            ),
        )
    )
    refs = [
        {
            "type": "file-refs",
            "id": fid,
            "attributes": {
                "name": name,
                "filesize": len(data),
                "mime-type": "application/pdf",
                "chdate": "2026-10-10T10:00:00+02:00",
                "is-downloadable": True,
                "description": "",
            },
            "relationships": {"parent": {"data": {"type": "folders", "id": parent}}},
        }
        for fid, (name, parent, data) in FILES.items()
    ]
    router.get(f"{API}/courses/{COURSE}/file-refs").mock(
        return_value=httpx.Response(200, json=_page(refs))
    )
    for fid, (_, _, data) in FILES.items():
        router.get(f"{API}/file-refs/{fid}/content").mock(
            return_value=httpx.Response(200, content=data)
        )
    router.get(f"{API}/courses/{OTHER}/news").mock(return_value=httpx.Response(200, json=_page([])))
    router.get(f"{API}/courses/{COURSE}/news").mock(
        return_value=httpx.Response(
            200,
            json=_page(
                [
                    {
                        "type": "news",
                        "id": "n1",
                        "attributes": {
                            "title": "Room change",
                            "content": "<p>Lecture moves to <b>room 42</b>.</p>",
                            "publication-start": "2026-10-12T09:00:00+02:00",
                        },
                    }
                ]
            ),
        )
    )


def install_courseware(router: respx.MockRouter) -> None:
    """One Courseware unit with a page that has a download block."""
    router.get(f"{API}/courses/{COURSE}/courseware-units").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": "courseware-units",
                        "id": "u1",
                        "attributes": {},
                        "relationships": {
                            "structural-element": {
                                "data": {"type": "courseware-structural-elements", "id": "root"}
                            }
                        },
                    }
                ]
            },
        )
    )
    page = {
        "type": "courseware-structural-elements",
        "id": "p1",
        "attributes": {"title": "Week 1"},
        "relationships": {
            "parent": {"data": {"type": "courseware-structural-elements", "id": "root"}}
        },
    }
    container = {
        "type": "courseware-containers",
        "id": "c1",
        "attributes": {},
        "relationships": {
            "structural-element": {"data": {"type": "courseware-structural-elements", "id": "p1"}}
        },
    }
    block = {
        "type": "courseware-blocks",
        "id": "b1",
        "attributes": {"block-type": "download", "payload": {"file_id": "cw-file"}},
        "relationships": {"container": {"data": {"type": "courseware-containers", "id": "c1"}}},
    }
    router.get(f"{API}/courseware-structural-elements/root").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "type": "courseware-structural-elements",
                    "id": "root",
                    "attributes": {"title": "Course"},
                    "relationships": {},
                },
                "included": [],
            },
        )
    )
    router.get(f"{API}/courseware-structural-elements/root/descendants").mock(
        return_value=httpx.Response(200, json={**_page([page]), "included": [container, block]})
    )
    router.get(f"{API}/file-refs/cw-file").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "type": "file-refs",
                    "id": "cw-file",
                    "attributes": {
                        "name": "Worksheet.pdf",
                        "filesize": 7,
                        "mime-type": "application/pdf",
                        "chdate": "2026-10-10T10:00:00+02:00",
                        "is-downloadable": True,
                    },
                }
            },
        )
    )
    router.get(f"{API}/file-refs/cw-file/content").mock(
        return_value=httpx.Response(200, content=b"%PDF-cw")
    )
