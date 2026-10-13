"""Planner + executor together: the properties the tool promises."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import COURSE_ID, FakeDownloads, make_config, remote

from studip_router.config import Config
from studip_router.executor import execute
from studip_router.planner import Kind, SkipReason, plan_course
from studip_router.state import State


def _plan(cfg: Config, state: State, files, **kw):
    return plan_course(cfg, cfg.courses[COURSE_ID], files, state.for_course(COURSE_ID), **kw)


def _sync(cfg: Config, state: State, files, downloads=None, **kw):
    plan = _plan(cfg, state, files, **kw)
    execute(plan, state, downloads or FakeDownloads(), run_id=1)
    return plan


def _kinds(plan) -> list[Kind]:
    return [a.kind for a in plan.actions]


def test_new_file_goes_to_default_target(cfg: Config, state: State, root: Path) -> None:
    downloads = FakeDownloads({"file-a.pdf": b"hello"})
    plan = _sync(cfg, state, [remote("a.pdf", size=5)], downloads)
    target = root / "Example Course" / "Sonstiges" / "a.pdf"
    assert _kinds(plan) == [Kind.DOWNLOAD]
    assert target.read_bytes() == b"hello"
    assert not (target.parent / "a.pdf.part").exists()
    # mtime mirrors the Stud.IP change date
    assert target.stat().st_mtime == datetime(2026, 10, 1, 12, tzinfo=UTC).timestamp()


def test_second_run_is_noop(cfg: Config, state: State) -> None:
    files = [remote("a.pdf"), remote("b.pdf")]
    _sync(cfg, state, files)
    downloads = FakeDownloads()
    plan = _sync(cfg, state, files, downloads)
    assert plan.actions == []
    assert plan.unchanged == 2
    assert downloads.calls == []


def test_dry_run_plan_equals_executed_plan(cfg: Config, state: State) -> None:
    files = [remote("a.pdf"), remote("b.pdf", size=5)]
    dry = _plan(cfg, state, files)
    real = _sync(cfg, state, files)
    assert [(a.kind, a.dest) for a in dry.actions] == [(a.kind, a.dest) for a in real.actions]


def test_existing_identical_file_is_adopted_even_if_nfd(
    cfg: Config, state: State, root: Path
) -> None:
    # Downloaded by hand earlier, stored with a decomposed (NFD) name in another subfolder.
    manual = root / "Example Course" / "Uebungen" / "übung-01.pdf"
    manual.write_bytes(b"abc")
    downloads = FakeDownloads()
    plan = _sync(cfg, state, [remote("übung-01.pdf", size=3)], downloads)
    assert _kinds(plan) == [Kind.ADOPT]
    assert downloads.calls == []
    assert len(list((root / "Example Course" / "Sonstiges").iterdir())) == 0
    # and afterwards it counts as synced
    assert _plan(cfg, state, [remote("übung-01.pdf", size=3)]).unchanged == 1


def test_name_conflict_gets_suffix(cfg: Config, state: State, root: Path) -> None:
    (root / "Example Course" / "Sonstiges" / "a.pdf").write_bytes(b"different size")
    plan = _sync(cfg, state, [remote("a.pdf", size=3)])
    assert _kinds(plan) == [Kind.DOWNLOAD]
    assert plan.actions[0].dest.name == "a_2.pdf"
    assert (root / "Example Course" / "Sonstiges" / "a.pdf").read_bytes() == b"different size"


def test_two_remote_files_same_name(cfg: Config, state: State) -> None:
    files = [remote("a.pdf", id="x", folder="A"), remote("a.pdf", id="y", folder="B")]
    plan = _sync(cfg, state, files)
    assert sorted(a.dest.name for a in plan.actions) == ["a.pdf", "a_2.pdf"]


@pytest.mark.parametrize("policy", ["error", "skip"])
def test_conflict_policies(root: Path, state: State, policy: str) -> None:
    cfg = make_config(root, on_conflict=policy)
    (root / "Example Course" / "Sonstiges" / "a.pdf").write_bytes(b"other")
    plan = _plan(cfg, state, [remote("a.pdf", size=3)])
    assert _kinds(plan) == [Kind.ERROR if policy == "error" else Kind.SKIP]


def test_update_creates_version_and_keeps_old(cfg: Config, state: State, root: Path) -> None:
    _sync(cfg, state, [remote("a.pdf")], FakeDownloads({"file-a.pdf": b"v1!"}))
    newer = remote("a.pdf", size=4, chdate=datetime(2026, 11, 1, tzinfo=UTC))
    plan = _sync(cfg, state, [newer], FakeDownloads({"file-a.pdf": b"v2!!"}))
    folder = root / "Example Course" / "Sonstiges"
    assert _kinds(plan) == [Kind.NEW_VERSION]
    assert (folder / "a.pdf").read_bytes() == b"v1!"
    assert (folder / "a_v2.pdf").read_bytes() == b"v2!!"
    # third version counts up from the base name, not a_v2_v3
    newest = remote("a.pdf", size=5, chdate=datetime(2026, 12, 1, tzinfo=UTC))
    plan = _sync(cfg, state, [newest], FakeDownloads({"file-a.pdf": b"v3!!!"}))
    assert plan.actions[0].dest.name == "a_v3.pdf"


def test_overwrite_policy_replaces_unmodified(root: Path, state: State) -> None:
    cfg = make_config(root, on_update="overwrite")
    _sync(cfg, state, [remote("a.pdf")], FakeDownloads({"file-a.pdf": b"old"}))
    newer = remote("a.pdf", size=3, chdate=datetime(2026, 11, 1, tzinfo=UTC))
    plan = _sync(cfg, state, [newer], FakeDownloads({"file-a.pdf": b"new"}))
    assert _kinds(plan) == [Kind.OVERWRITE]
    assert (root / "Example Course" / "Sonstiges" / "a.pdf").read_bytes() == b"new"


def test_overwrite_policy_never_clobbers_local_edits(root: Path, state: State) -> None:
    cfg = make_config(root, on_update="overwrite")
    _sync(cfg, state, [remote("a.pdf")], FakeDownloads({"file-a.pdf": b"old"}))
    local = root / "Example Course" / "Sonstiges" / "a.pdf"
    local.write_bytes(b"my notes")
    newer = remote("a.pdf", chdate=datetime(2026, 11, 1, tzinfo=UTC))
    plan = _sync(cfg, state, [newer], FakeDownloads({"file-a.pdf": b"new"}))
    assert _kinds(plan) == [Kind.NEW_VERSION]
    assert local.read_bytes() == b"my notes"


def test_same_size_touch_is_not_a_modification(root: Path, state: State) -> None:
    """iCloud may bump mtime without changing content: hash decides."""
    cfg = make_config(root, on_update="overwrite")
    _sync(cfg, state, [remote("a.pdf")], FakeDownloads({"file-a.pdf": b"old"}))
    local = root / "Example Course" / "Sonstiges" / "a.pdf"
    os.utime(local, (1, 1))
    newer = remote("a.pdf", chdate=datetime(2026, 11, 1, tzinfo=UTC))
    assert _kinds(_plan(cfg, state, [newer])) == [Kind.OVERWRITE]


def test_deleted_local_file_is_restored(cfg: Config, state: State, root: Path) -> None:
    target = root / "Example Course" / "Sonstiges" / "a.pdf"
    _sync(cfg, state, [remote("a.pdf")], FakeDownloads({"file-a.pdf": b"abc"}))
    target.unlink()
    plan = _sync(cfg, state, [remote("a.pdf")], FakeDownloads({"file-a.pdf": b"abc"}))
    assert _kinds(plan) == [Kind.DOWNLOAD]
    assert plan.actions[0].note == "deleted locally, restoring"
    assert target.read_bytes() == b"abc"
    assert _plan(cfg, state, [remote("a.pdf")]).actions == []  # and stable afterwards


def test_deleted_newer_version_restores_latest(cfg: Config, state: State, root: Path) -> None:
    _sync(cfg, state, [remote("a.pdf")])
    (root / "Example Course" / "Sonstiges" / "a.pdf").unlink()
    newer = remote("a.pdf", size=4, chdate=datetime(2026, 11, 1, tzinfo=UTC))
    plan = _sync(cfg, state, [newer], FakeDownloads({"file-a.pdf": b"new!"}))
    assert _kinds(plan) == [Kind.DOWNLOAD]
    assert (root / "Example Course" / "Sonstiges" / "a.pdf").read_bytes() == b"new!"


def test_moved_local_file_is_followed_not_duplicated(cfg: Config, state: State, root: Path) -> None:
    course = root / "Example Course"
    _sync(cfg, state, [remote("a.pdf")])
    (course / "Sonstiges" / "a.pdf").rename(course / "Uebungen" / "a.pdf")
    downloads = FakeDownloads()
    plan = _sync(cfg, state, [remote("a.pdf")], downloads)
    assert _kinds(plan) == [Kind.ADOPT] and downloads.calls == []
    assert not (course / "Sonstiges" / "a.pdf").exists()
    assert _plan(cfg, state, [remote("a.pdf")]).actions == []


def test_exclusions(cfg: Config, state: State) -> None:
    files = [
        remote("lecture.mp4", mime="video/mp4"),
        remote("huge.zip", size=300 * 1024 * 1024, mime="application/zip"),
        remote("locked.pdf", downloadable=False),
    ]
    plan = _plan(cfg, state, files)
    assert {a.file.name: a.reason for a in plan.actions} == {
        "lecture.mp4": SkipReason.EXCLUDED,
        "huge.zip": SkipReason.TOO_LARGE,
        "locked.pdf": SkipReason.NOT_DOWNLOADABLE,
    }
    assert _kinds(_plan(cfg, state, files[1:2], include_large=True)) == [Kind.DOWNLOAD]


def test_missing_target_dir_is_an_error_by_default(root: Path, state: State) -> None:
    cfg = make_config(root, default_target="Skript / Alle Folien")
    plan = _plan(cfg, state, [remote("a.pdf")])
    assert _kinds(plan) == [Kind.ERROR]
    assert "'A : B' on disk" in plan.actions[0].reason


def test_missing_target_dir_created_when_allowed(root: Path, state: State) -> None:
    cfg = make_config(root, default_target="_inbox", create_missing_dirs=True)
    plan = _sync(cfg, state, [remote("a.pdf"), remote("b.pdf")])
    assert _kinds(plan) == [Kind.MKDIR, Kind.DOWNLOAD, Kind.DOWNLOAD]
    assert (root / "Example Course" / "_inbox" / "b.pdf").exists()


def test_remote_deleted_is_reported_never_removed(cfg: Config, state: State, root: Path) -> None:
    _sync(cfg, state, [remote("a.pdf")])
    plan = _plan(cfg, state, [])
    assert [r.remote_name for r in plan.remote_deleted] == ["a.pdf"]
    assert (root / "Example Course" / "Sonstiges" / "a.pdf").exists()


def test_slash_in_remote_name_stays_one_file(cfg: Config, state: State, root: Path) -> None:
    plan = _sync(cfg, state, [remote("Blatt 1/2.pdf")])
    assert plan.actions[0].dest == root / "Example Course" / "Sonstiges" / "Blatt 01-02.pdf"


def test_interrupt_leaves_no_partial_file(cfg: Config, state: State, root: Path) -> None:
    def interrupted(file_id, sink):
        sink(b"half")
        raise KeyboardInterrupt

    plan = _plan(cfg, state, [remote("a.pdf")])
    with pytest.raises(KeyboardInterrupt):
        execute(plan, state, interrupted, run_id=1)
    folder = root / "Example Course" / "Sonstiges"
    assert list(folder.iterdir()) == []
    assert state.for_course(COURSE_ID) == {}


def test_stale_plan_never_overwrites(cfg: Config, state: State, root: Path) -> None:
    plan = _plan(cfg, state, [remote("a.pdf")])
    late = root / "Example Course" / "Sonstiges" / "a.pdf"
    late.write_bytes(b"appeared meanwhile")
    outcome = execute(plan, state, FakeDownloads(), run_id=1)
    assert len(outcome.failed) == 1
    assert late.read_bytes() == b"appeared meanwhile"


SEMESTER_START = datetime(2026, 10, 1, tzinfo=UTC)


def test_adopt_matches_underscores_for_spaces(cfg: Config, state: State, root: Path) -> None:
    (root / "Example Course" / "Uebungen" / "Homework_1.pdf").write_bytes(b"abc")
    plan = _plan(cfg, state, [remote("Homework 1.pdf", size=3)])
    assert _kinds(plan) == [Kind.ADOPT]


def test_fresh_local_file_with_other_size_becomes_older_version(
    cfg: Config, state: State, root: Path
) -> None:
    local = root / "Example Course" / "Skript : Alle Folien" / "lecture-01.pdf"
    local.write_bytes(b"old slides")
    plan = _sync(cfg, state, [remote("lecture-01.pdf", size=3)], fresh_since=SEMESTER_START)
    assert _kinds(plan) == [Kind.NEW_VERSION]
    assert plan.actions[0].dest == local.with_name("lecture-01_v2.pdf")
    assert local.read_bytes() == b"old slides"
    assert _plan(cfg, state, [remote("lecture-01.pdf", size=3)]).actions == []


def test_old_semester_file_with_same_name_is_left_alone(
    cfg: Config, state: State, root: Path
) -> None:
    old = root / "Example Course" / "Sonstiges" / "Aufgabe 1.pdf"
    old.write_bytes(b"from 2024")
    os.utime(old, (datetime(2024, 11, 1).timestamp(),) * 2)
    plan = _plan(cfg, state, [remote("Aufgabe 1.pdf", size=3)], fresh_since=SEMESTER_START)
    assert _kinds(plan) == [Kind.DOWNLOAD]
    assert plan.actions[0].dest.name == "Aufgabe 01.pdf"
