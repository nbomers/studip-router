"""Unlocking a course's password-protected PDFs (one shared password per course)."""

from __future__ import annotations

import io
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import keyring
import pytest
from conftest import COURSE_ID, FakeDownloads, make_config, remote
from keyring.backend import KeyringBackend
from pypdf import PdfReader, PdfWriter

from studip_router import auth
from studip_router.config import Config, parse
from studip_router.executor import Outcome, execute, unlock_course
from studip_router.planner import Kind, plan_course
from studip_router.postprocess import Unlock, unlock_pdf
from studip_router.report import unlocked_lines
from studip_router.state import State
from studip_router.sync import SyncPlan, execute_plan

COURSE_PW = "mci-2026"


def pdf(user_password: str | None = None, owner_password: str = "owner") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": "Lecture 1"})
    if user_password is not None:
        writer.encrypt(user_password, owner_password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _sync(cfg: Config, state: State, files, downloads, password: str | None = COURSE_PW):
    plan = plan_course(cfg, cfg.courses[COURSE_ID], files, state.for_course(COURSE_ID))
    return plan, execute(plan, state, downloads, run_id=1, pdf_password=password)


# -- unlock_pdf: every kind of file ------------------------------------------------------


def test_locked_pdf_is_unlocked_in_place(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(pdf(COURSE_PW))
    assert unlock_pdf(path, COURSE_PW) is Unlock.DECRYPTED
    reader = PdfReader(path)
    assert not reader.is_encrypted
    assert len(reader.pages) == 1
    assert reader.metadata.title == "Lecture 1"
    assert list(tmp_path.iterdir()) == [path]  # no temp files left


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (pdf("another-password"), Unlock.WRONG_PASSWORD),
        (pdf(), Unlock.NOT_ENCRYPTED),
        (pdf(user_password=""), Unlock.OPENS_WITHOUT_PASSWORD),  # print/copy limits only
        (b"PK\x03\x04 not a pdf", Unlock.NOT_PDF),
    ],
)
def test_other_files_stay_byte_for_byte(tmp_path: Path, content: bytes, expected: Unlock) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(content)
    assert unlock_pdf(path, COURSE_PW) is expected
    assert path.read_bytes() == content


# -- during a sync ----------------------------------------------------------------------


def test_sync_unlocks_only_pdfs_with_the_course_password(
    cfg: Config, state: State, root: Path
) -> None:
    content = {
        "file-locked.pdf": pdf(COURSE_PW),
        "file-other.pdf": pdf("another-password"),
        "file-plain.pdf": pdf(),
    }
    files = [
        remote(n, size=len(content[f"file-{n}"])) for n in ("locked.pdf", "other.pdf", "plain.pdf")
    ]
    _, outcome = _sync(cfg, state, files, FakeDownloads(content))
    folder = root / "Example Course" / "Sonstiges"
    assert outcome.unlocked == [folder / "locked.pdf"]
    assert [p.name for p, _ in outcome.still_locked] == ["other.pdf"]
    assert not PdfReader(folder / "locked.pdf").is_encrypted
    assert (folder / "other.pdf").read_bytes() == content["file-other.pdf"]
    assert (folder / "plain.pdf").read_bytes() == content["file-plain.pdf"]
    # the unlocked file keeps the Stud.IP date
    assert (folder / "locked.pdf").stat().st_mtime == datetime(
        2026, 10, 1, 12, tzinfo=UTC
    ).timestamp()

    # next run: nothing to do, the unlocked file does not count as edited
    downloads = FakeDownloads(content)
    plan, _ = _sync(cfg, state, files, downloads)
    assert plan.actions == []
    assert plan.unchanged == 3
    assert downloads.calls == []


def test_without_a_password_nothing_is_unlocked(cfg: Config, state: State, root: Path) -> None:
    content = {"file-locked.pdf": pdf(COURSE_PW)}
    _, outcome = _sync(
        cfg,
        state,
        [remote("locked.pdf", size=len(content["file-locked.pdf"]))],
        FakeDownloads(content),
        password=None,
    )
    assert outcome.unlocked == []
    assert (root / "Example Course" / "Sonstiges" / "locked.pdf").read_bytes() == content[
        "file-locked.pdf"
    ]


def test_identical_reupload_of_a_locked_pdf_creates_no_copy(
    cfg: Config, state: State, root: Path
) -> None:
    content = {"file-locked.pdf": pdf(COURSE_PW)}
    first = remote("locked.pdf", size=len(content["file-locked.pdf"]))
    _sync(cfg, state, [first], FakeDownloads(content))
    later = replace(first, chdate=datetime(2026, 10, 8, 12, tzinfo=UTC))
    plan, _ = _sync(cfg, state, [later], FakeDownloads(content))
    folder = root / "Example Course" / "Sonstiges"
    assert sorted(p.name for p in folder.iterdir()) == ["locked.pdf"]
    assert all(a.kind is Kind.REFRESH for a in plan.actions)


def test_overwrite_mode_replaces_an_unlocked_pdf_but_not_an_edited_one(
    root: Path, state: State
) -> None:
    cfg = make_config(root, on_update="overwrite")
    content = {"file-locked.pdf": pdf(COURSE_PW)}
    first = remote("locked.pdf", size=len(content["file-locked.pdf"]))
    _sync(cfg, state, [first], FakeDownloads(content))
    path = root / "Example Course" / "Sonstiges" / "locked.pdf"
    os.utime(path, (0, 0))  # e.g. touched by iCloud: falls back to the hash
    changed = {"file-locked.pdf": pdf(COURSE_PW) + b"\n"}
    later = replace(
        first, size=len(changed["file-locked.pdf"]), chdate=datetime(2026, 10, 8, tzinfo=UTC)
    )
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [later], state.for_course(COURSE_ID))
    assert [a.kind for a in plan.actions] == [Kind.OVERWRITE]  # unlocked != edited

    path.write_bytes(path.read_bytes() + b"\n% my notes\n")
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [later], state.for_course(COURSE_ID))
    assert [a.kind for a in plan.actions] == [Kind.NEW_VERSION]  # never overwrites edits


def test_hand_downloaded_locked_pdf_is_adopted_and_unlocked(
    cfg: Config, state: State, root: Path
) -> None:
    data = pdf(COURSE_PW)
    manual = root / "Example Course" / "Uebungen" / "sheet.pdf"
    manual.write_bytes(data)
    downloads = FakeDownloads()
    plan, outcome = _sync(cfg, state, [remote("sheet.pdf", size=len(data))], downloads)
    assert [a.kind for a in plan.actions] == [Kind.ADOPT]
    assert downloads.calls == []
    assert outcome.unlocked == [manual]
    assert not PdfReader(manual).is_encrypted


# -- after setting the password: PDFs synced earlier --------------------------------------


def test_unlock_course_handles_files_synced_before(cfg: Config, state: State, root: Path) -> None:
    content = {"file-a.pdf": pdf(COURSE_PW), "file-b.pdf": pdf(COURSE_PW)}
    files = [remote(n, size=len(content[f"file-{n}"])) for n in ("a.pdf", "b.pdf")]
    _sync(cfg, state, files, FakeDownloads(content), password=None)
    (root / "Example Course" / "Sonstiges" / "b.pdf").unlink()  # deleted locally meanwhile

    outcome = unlock_course(state, COURSE_ID, COURSE_PW)
    assert [p.name for p in outcome.unlocked] == ["a.pdf"]
    assert unlock_course(state, COURSE_ID, COURSE_PW).unlocked == []  # only once

    plan, _ = _sync(cfg, state, files[:1], FakeDownloads(content))
    assert plan.actions == [] and plan.unchanged == 1


def test_hint_when_the_password_fits_nothing(tmp_path: Path) -> None:
    outcome = Outcome(still_locked=[(tmp_path / "x.pdf", str(Unlock.WRONG_PASSWORD))])
    assert any("own password" in line for line in unlocked_lines(outcome))
    outcome.unlocked.append(tmp_path / "y.pdf")
    assert not any("own password" in line for line in unlocked_lines(outcome))


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        del self.items[(service, username)]


def test_every_course_has_its_own_password(
    root: Path, state: State, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_id = "0de11ca7e550de11ca7e550de11ca7e5"
    (root / "Other Course" / "Sonstiges").mkdir(parents=True)
    cfg = parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root)},
            "courses": {
                COURSE_ID: {"name": "Example Course", "target": "Example Course"},
                other_id: {"name": "Other Course", "target": "Other Course"},
            },
        },
        Path("config.toml"),
    )
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    try:
        auth.set_pdf_password(COURSE_ID, "first-password")
        auth.set_pdf_password(other_id, "second-password")
        content = {
            "file-a.pdf": pdf("first-password"),
            "file-b.pdf": pdf("second-password"),
            "file-c.pdf": pdf("second-password"),  # in the first course: wrong password
        }
        a = remote("a.pdf", size=len(content["file-a.pdf"]))
        c = remote("c.pdf", size=len(content["file-c.pdf"]))
        b = replace(remote("b.pdf", size=len(content["file-b.pdf"])), course_id=other_id)
        plans = [
            plan_course(cfg, cfg.courses[COURSE_ID], [a, c], state.for_course(COURSE_ID)),
            plan_course(cfg, cfg.courses[other_id], [b], state.for_course(other_id)),
        ]

        class Client:
            download = FakeDownloads(content)

        result = execute_plan(SyncPlan(None, plans), cfg, state, Client())  # type: ignore[arg-type]

        assert sorted(p.relative_to(root).as_posix() for p in result.outcome.unlocked) == [
            "Example Course/Sonstiges/a.pdf",
            "Other Course/Sonstiges/b.pdf",
        ]
        assert [p.name for p, _ in result.outcome.still_locked] == ["c.pdf"]

        assert auth.delete_pdf_password(COURSE_ID)  # removing one keeps the other
        assert auth.get_pdf_password(COURSE_ID) is None
        assert auth.get_pdf_password(other_id) == "second-password"
    finally:
        keyring.set_keyring(previous)
