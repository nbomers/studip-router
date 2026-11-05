"""Semester change assistant (0.5.0): new semester folder, new courses, archive."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from studip_router import semester as sem_mod
from studip_router.config import load
from studip_router.models import Course, Semester

WISE = Semester(
    "w", "WiSe 2026/27", datetime(2026, 10, 1, tzinfo=UTC), datetime(2027, 3, 31, tzinfo=UTC)
)
SOSE = Semester(
    "s", "SoSe 2027", datetime(2027, 4, 1, tzinfo=UTC), datetime(2027, 9, 30, tzinfo=UTC)
)
SEMESTERS = [WISE, SOSE]
OLD_ID = "a" * 32
LONG_ID = "b" * 32
NEW_ID = "c" * 32
MARCH = datetime(2027, 3, 1, tzinfo=UTC)  # SoSe courses are visible, WiSe still runs
MAY = datetime(2027, 5, 1, tzinfo=UTC)

OLD = Course(OLD_ID, "Logik (V)", WISE, WISE)
LONG = Course(LONG_ID, "Kolloquium", WISE, None)  # unlimited: never old
NEW = Course(NEW_ID, "Analysis II (V)", SOSE, SOSE)


def write_config(tmp_path: Path, root: Path) -> Path:
    path = Path(tmp_path / "cfg" / "config.toml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# my config\n"
        f"[server]\nbase_url = 'https://studip.example.edu'\nusername = 'jdoe'\n"
        f"[sync]\nroot = '{root}'\n"
        f"[courses.{OLD_ID}]\nname = 'Logik'\ntarget = 'Logik'\n"
        f"[courses.{LONG_ID}]\nname = 'Kolloquium'\ntarget = 'Kolloquium'\n"
    )
    for name in ("Logik", "Kolloquium"):
        for sub in ("Folien", "Übungen"):
            (root / name / sub).mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def setup(tmp_path: Path):
    root = tmp_path / "Uni" / "Semester 5"
    path = write_config(tmp_path, root)
    return load(path), root


@pytest.mark.parametrize(
    ("name", "title", "expected"),
    [
        ("Semester 5", "SoSe 2027", "Semester 6"),
        ("5. Semester", "SoSe 2027", "6. Semester"),
        ("WiSe 2026-27", "SoSe 2027", "SoSe 2027"),
        ("WS 26-27", "SoSe 2027", "SoSe 2027"),
        ("Uni", "SoSe 2027", None),
        ("Studium", "SoSe 2027", None),
    ],
)
def test_root_proposal(tmp_path: Path, name: str, title: str, expected: str | None) -> None:
    proposed = sem_mod.propose_root(tmp_path / name, title)
    assert (proposed.name if proposed else None) == expected


def test_no_change_mid_semester(setup) -> None:
    cfg, _ = setup
    assert (
        sem_mod.find_change(cfg, [OLD, LONG], SEMESTERS, datetime(2026, 11, 1, tzinfo=UTC)) is None
    )


def test_change_before_the_semester_keeps_running_courses_by_default(setup) -> None:
    cfg, root = setup
    change = sem_mod.find_change(cfg, [OLD, LONG, NEW], SEMESTERS, MARCH)
    assert change is not None and change.semester == SOSE
    assert [c.id for c in change.new_courses] == [NEW_ID]
    assert [o.config.id for o in change.old_courses] == [OLD_ID]  # not the unlimited one
    assert not change.old_courses[0].ended
    assert change.proposed_root == root.parent / "Semester 6"
    assert change.targets == {NEW_ID: "Analysis II"}
    assert change.subfolders == ("Folien", "Übungen")  # copied from the course folders
    choices = sem_mod.default_choices(change)
    assert choices.archive == set()  # still running: exams may follow


def test_ended_course_alone_is_offered_for_archiving(setup) -> None:
    cfg, _ = setup
    change = sem_mod.find_change(cfg, [OLD, LONG], SEMESTERS, MAY)
    assert change is not None and not change.new_courses and change.proposed_root is None
    assert sem_mod.default_choices(change).archive == {OLD_ID}
    # kept once: not asked again
    assert sem_mod.find_change(cfg, [OLD, LONG], SEMESTERS, MAY, kept={OLD_ID}) is None


def test_apply_moves_the_root_and_keeps_old_folders(setup, tmp_path: Path) -> None:
    cfg, root = setup
    (root / "Logik" / "Folien" / "vl01.pdf").write_bytes(b"%PDF")
    change = sem_mod.find_change(cfg, [OLD, LONG, NEW], SEMESTERS, MAY)
    assert change is not None and change.old_courses[0].ended
    choices = sem_mod.default_choices(change)
    choices.read_only = True
    applied = sem_mod.apply_change(cfg, change, choices)
    new_root = root.parent / "Semester 6"
    assert applied.problems == []
    assert applied.created == [new_root / "Analysis II"]
    assert sorted(p.name for p in (new_root / "Analysis II").iterdir()) == ["Folien", "Übungen"]
    new = applied.config
    assert new.root == new_root
    assert new.course_dir(new.courses[OLD_ID]) == root / "Logik"
    assert new.course_dir(new.courses[LONG_ID]) == root / "Kolloquium"  # still synced, same place
    assert not new.courses[LONG_ID].exclude
    assert new.courses[OLD_ID].exclude
    assert new.courses[NEW_ID].target == "Analysis II"
    assert "# my config" in cfg.path.read_text()  # comments survive
    pdf = root / "Logik" / "Folien" / "vl01.pdf"
    assert applied.read_only_files == 1 and not pdf.stat().st_mode & stat.S_IWUSR


def test_keeping_the_root_puts_new_courses_next_to_the_old(setup) -> None:
    cfg, root = setup
    repeat = Course(NEW_ID, "Logik (V)", SOSE, SOSE)  # same title as the old course
    change = sem_mod.find_change(cfg, [OLD, repeat], SEMESTERS, MAY)
    assert change is not None
    choices = sem_mod.default_choices(change)
    choices.new_root = None
    choices.add = sem_mod.new_targets(change, new_root=False)
    assert choices.add == {NEW_ID: "Logik (2)"}  # never the old course's folder
    applied = sem_mod.apply_change(cfg, change, choices)
    assert applied.config.root == root
    assert applied.config.courses[OLD_ID].target == "Logik"


def test_ended_course_kept_is_remembered(setup) -> None:
    cfg, _ = setup
    change = sem_mod.find_change(cfg, [OLD, LONG], SEMESTERS, MAY)
    assert change is not None
    choices = sem_mod.default_choices(change)
    choices.archive.clear()
    choices.keep.add(OLD_ID)
    sem_mod.apply_change(cfg, change, choices)
    assert sem_mod.load_kept() == {OLD_ID}


def test_unsafe_root_writes_nothing(setup) -> None:
    cfg, _ = setup
    before = cfg.path.read_text()
    change = sem_mod.find_change(cfg, [OLD, NEW], SEMESTERS, MAY)
    assert change is not None
    choices = sem_mod.default_choices(change)
    choices.new_root = Path("/etc/uni")
    with pytest.raises(ValueError):
        sem_mod.apply_change(cfg, change, choices)
    assert cfg.path.read_text() == before


def test_cli_semester_dry_run_and_yes(setup, monkeypatch: pytest.MonkeyPatch) -> None:
    from studip_router.cli import app

    cfg, root = setup
    monkeypatch.setenv("STUDIP_ROUTER_CONFIG", str(cfg.path))

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def me(self):
            return {"id": "u1"}

        def courses(self, _uid):
            return [OLD, LONG, NEW]

        def semesters(self):
            return SEMESTERS

    monkeypatch.setattr("studip_router.cli.open_client", lambda _cfg: FakeClient())
    monkeypatch.setattr(sem_mod, "datetime", type("D", (), {"now": staticmethod(lambda: MAY)}))
    runner = CliRunner()
    result = runner.invoke(app, ["semester", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Analysis II" in result.output and "Dry run" in result.output
    assert "Semester 6" not in cfg.path.read_text()
    result = runner.invoke(app, ["semester", "-y", "--keep-root"])
    assert result.exit_code == 0, result.output
    assert (root / "Analysis II" / "Folien").is_dir()
    assert load(cfg.path).courses[OLD_ID].exclude
