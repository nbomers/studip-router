"""An extracted archive is removed and not downloaded again until it changes."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from conftest import COURSE_ID, FakeDownloads, make_config, remote

from studip_router.executor import execute
from studip_router.planner import Kind, plan_course
from studip_router.reorganize import plan_reorganize
from studip_router.rules import parse_rules


def _zip(**members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _sync(cfg, state, files, downloads, run_id):
    course = cfg.courses[COURSE_ID]
    plan = plan_course(cfg, course, files, state.for_course(COURSE_ID))
    return plan, execute(plan, state, downloads, run_id)


def test_extracted_archive_is_removed_and_not_fetched_again(state, root: Path) -> None:
    cfg = make_config(root, unzip=True)
    first = _zip(**{"a.txt": "a"})
    f = replace(remote("Blatt.zip", size=len(first), mime="application/zip"), content_id="c1")
    downloads = FakeDownloads({f.id: first})
    _plan, outcome = _sync(cfg, state, [f], downloads, 1)
    folder = root / "Example Course" / "Sonstiges"
    assert not (folder / "Blatt.zip").exists()
    assert (folder / "Blatt" / "a.txt").read_text() == "a"
    assert outcome.extracted == [folder / "Blatt"]

    plan, _ = _sync(cfg, state, [f], downloads, 2)
    assert plan.actions == [] and plan.unchanged == 1
    assert downloads.calls == [f.id]

    # only details changed on Stud.IP (same content): still nothing to fetch
    redescribed = replace(f, chdate=datetime(2026, 10, 9, tzinfo=UTC))
    plan, _ = _sync(cfg, state, [redescribed], downloads, 3)
    assert plan.actions == [] and downloads.calls == [f.id]

    # the archive really changed: a new version next to the old folder
    (folder / "Blatt" / "a.txt").write_text("my notes")
    second = _zip(**{"a.txt": "a2", "b.txt": "b"})
    changed = replace(
        f, size=len(second), content_id="c2", chdate=datetime(2026, 10, 10, tzinfo=UTC)
    )
    downloads.content[f.id] = second
    plan, _ = _sync(cfg, state, [changed], downloads, 4)
    assert [a.kind for a in plan.actions] == [Kind.NEW_VERSION]
    assert (folder / "Blatt" / "a.txt").read_text() == "my notes"
    assert (folder / "Blatt_v2" / "b.txt").read_text() == "b"
    assert not (folder / "Blatt_v2.zip").exists()
    plan, _ = _sync(cfg, state, [changed], downloads, 5)
    assert plan.actions == []


def test_deleting_the_folder_too_restores_the_archive(state, root: Path) -> None:
    import shutil

    cfg = make_config(root, unzip=True)
    data = _zip(**{"a.txt": "a"})
    f = remote("Blatt.zip", size=len(data), mime="application/zip")
    downloads = FakeDownloads({f.id: data})
    _sync(cfg, state, [f], downloads, 1)
    folder = root / "Example Course" / "Sonstiges"
    shutil.rmtree(folder / "Blatt")
    plan, _ = _sync(cfg, state, [f], downloads, 2)
    assert [a.kind for a in plan.actions] == [Kind.DOWNLOAD]
    assert (folder / "Blatt" / "a.txt").read_text() == "a"


def test_an_archive_extracted_by_an_older_version_can_be_deleted_by_hand(state, root: Path) -> None:
    cfg = make_config(root, unzip=True)
    data = _zip(**{"a.txt": "a"})
    f = remote("Blatt.zip", size=len(data), mime="application/zip")
    folder = root / "Example Course" / "Sonstiges"
    # older releases kept the archive: zip and folder both on disk
    (folder / "Blatt").mkdir(parents=True)
    _sync(cfg, state, [f], FakeDownloads({f.id: data}), 1)
    assert (folder / "Blatt.zip").exists()  # the folder existed: nothing extracted, zip kept
    (folder / "Blatt.zip").unlink()
    downloads = FakeDownloads({f.id: data})
    plan, _ = _sync(cfg, state, [f], downloads, 2)
    assert plan.actions == [] and downloads.calls == []


def test_reorganize_ignores_removed_archives(state, root: Path) -> None:
    cfg = make_config(root, unzip=True)
    data = _zip(**{"a.txt": "a"})
    f = remote("Blatt.zip", size=len(data), mime="application/zip")
    _sync(cfg, state, [f], FakeDownloads({f.id: data}), 1)
    (root / "Example Course" / "Uebungen").mkdir(exist_ok=True)
    course = cfg.courses[COURSE_ID]
    rule = parse_rules([{"id": "z", "match": {"name_glob": "*.zip"}, "target": "Uebungen"}], "r")[0]
    cfg = replace(cfg, courses={COURSE_ID: replace(course, rules=tuple(rule))})
    assert plan_reorganize(cfg, state) == []
