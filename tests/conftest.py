from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from studip_router.config import Config, parse
from studip_router.models import RemoteFile
from studip_router.state import State

COURSE_ID = "c0ffee00c0ffee00c0ffee00c0ffee00"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch the real config, state or keychain from tests."""
    monkeypatch.setenv("STUDIP_ROUTER_CONFIG", str(tmp_path / "cfg" / "config.toml"))
    monkeypatch.setenv("STUDIP_ROUTER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("studip_router.executor._sleep", lambda _s: None)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    course = tmp_path / "uni" / "Example Course"
    for sub in ("Sonstiges", "Uebungen", "Skript : Alle Folien"):
        (course / sub).mkdir(parents=True)
    return tmp_path / "uni"


def make_config(root: Path, **sync: object) -> Config:
    return parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root), **sync},
            "courses": {COURSE_ID: {"name": "Example Course", "target": "Example Course"}},
        },
        Path("config.toml"),
    )


@pytest.fixture
def cfg(root: Path) -> Config:
    return make_config(root)


@pytest.fixture
def state(tmp_path: Path) -> State:
    with State(tmp_path / "state.sqlite") as s:
        yield s


def remote(
    name: str,
    *,
    id: str | None = None,
    size: int = 3,
    chdate: datetime = datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    mime: str = "application/pdf",
    folder: str = "",
    downloadable: bool = True,
) -> RemoteFile:
    return RemoteFile(
        id=id or f"file-{name}",
        course_id=COURSE_ID,
        name=name,
        folder_path=folder,
        size=size,
        chdate=chdate,
        mime_type=mime,
        description="",
        downloadable=downloadable,
    )


class FakeDownloads:
    """Stand-in for StudipClient.download serving fixed content per file id."""

    def __init__(self, content: dict[str, bytes] | None = None) -> None:
        self.content = content or {}
        self.calls: list[str] = []

    def __call__(self, file_id: str, sink) -> None:
        self.calls.append(file_id)
        sink(self.content.get(file_id, b"abc"))
