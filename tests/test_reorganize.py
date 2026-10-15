from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from conftest import COURSE_ID, FakeDownloads, remote

from studip_router.config import parse
from studip_router.executor import execute
from studip_router.planner import plan_course
from studip_router.reorganize import apply_reorganize, apply_undo, plan_reorganize, plan_undo


def _cfg(root: Path, rules: list[dict]):
    return parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root)},
            "courses": {COURSE_ID: {"target": "Example Course", "rules": rules}},
        },
        Path("c.toml"),
    )


SHEETS = [
    {
        "id": "sheets",
        "match": {"name_regex": r"blatt ?(\d+)"},
        "target": "Uebungen",
        "rename": "Uebung_{1:02}{ext}",
    }
]


def test_reorganize_then_undo(root: Path, state) -> None:
    course = root / "Example Course"
    before = _cfg(root, [])
    files = [remote("Blatt 1.pdf"), remote("notes.pdf")]
    execute(
        plan_course(before, before.courses[COURSE_ID], files, {}), state, FakeDownloads(), run_id=1
    )
    assert (course / "Sonstiges" / "Blatt 01.pdf").exists()

    after = _cfg(root, SHEETS)
    moves = plan_reorganize(after, state)
    assert [(m.src.name, m.dst.relative_to(course).as_posix(), m.problem) for m in moves] == [
        ("Blatt 01.pdf", "Uebungen/Uebung_01.pdf", "")
    ]
    done, failed = apply_reorganize(moves, state)
    assert (done, failed) == (1, [])
    assert (course / "Uebungen" / "Uebung_01.pdf").exists()
    assert not (course / "Sonstiges" / "Blatt 01.pdf").exists()
    # state follows the file, so the next sync is still a no-op
    plan = plan_course(after, after.courses[COURSE_ID], files, state.for_course(COURSE_ID))
    assert plan.actions == [] and plan.unchanged == 2
    assert plan_reorganize(after, state) == []

    run_id, steps = plan_undo(state)
    assert apply_undo(state, run_id, steps) == 1
    assert (course / "Sonstiges" / "Blatt 01.pdf").exists()
    assert plan_undo(state) == (None, [])  # already undone


def test_reorganize_never_overwrites(root: Path, state) -> None:
    course = root / "Example Course"
    before = _cfg(root, [])
    execute(
        plan_course(before, before.courses[COURSE_ID], [remote("Blatt 1.pdf")], {}),
        state,
        FakeDownloads(),
        run_id=1,
    )
    (course / "Uebungen" / "Uebung_01.pdf").write_bytes(b"mine")
    [move] = plan_reorganize(_cfg(root, SHEETS), state)
    assert move.problem == "target already exists"
    assert apply_reorganize([move], state) == (0, [])
    assert (course / "Uebungen" / "Uebung_01.pdf").read_bytes() == b"mine"


def test_reorganize_moves_files_that_now_route_by_folder_name(root: Path, state) -> None:
    """Synced into the default folder by an older version, moved once auto-routing exists."""
    course = root / "Example Course"
    old = parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root), "auto_route": False},
            "courses": {COURSE_ID: {"target": "Example Course"}},
        },
        Path("c.toml"),
    )
    files = [remote("00_Overview.pdf", folder="Slides"), remote("Orga.pdf")]
    execute(plan_course(old, old.courses[COURSE_ID], files, {}), state, FakeDownloads(), run_id=1)
    assert (course / "Sonstiges" / "00_Overview.pdf").exists()

    moves = plan_reorganize(_cfg(root, []), state)
    assert [(m.src.name, m.dst.parent.name, m.rule_id, m.problem) for m in moves] == [
        ("00_Overview.pdf", "Skript : Alle Folien", "auto:slides", "")
    ]
    apply_reorganize(moves, state)
    assert (course / "Skript : Alle Folien" / "00_Overview.pdf").exists()


def test_pending_moves_only_count_changed_routing(root: Path, state) -> None:
    from studip_router.reorganize import pending_moves

    course = root / "Example Course"
    cfg = _cfg(root, SHEETS)
    files = [remote("Blatt 1.pdf"), remote("notes.pdf")]
    execute(plan_course(cfg, cfg.courses[COURSE_ID], files, {}), state, FakeDownloads(), run_id=1)
    assert pending_moves(cfg, state) == []

    # moved by hand while the rule stayed the same: the user's choice, no hint
    (course / "Uebungen" / "Uebung_01.pdf").rename(course / "Sonstiges" / "Uebung_01.pdf")
    record = state.get("file-Blatt 1.pdf")
    state.upsert(replace(record, local_path=str(course / "Sonstiges" / "Uebung_01.pdf")))
    assert pending_moves(cfg, state) == []

    # a new rule now claims notes.pdf: that is a hint
    notes = [{"id": "notes", "match": {"name_glob": "notes*"}, "target": "Uebungen"}]
    assert [m.src.name for m in pending_moves(_cfg(root, SHEETS + notes), state)] == ["notes.pdf"]
