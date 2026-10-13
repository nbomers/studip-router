from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from conftest import COURSE_ID, remote

from studip_router.config import load
from studip_router.suggest import suggest_rules
from studip_router.tomlwrite import append_blocks, rule_block


@pytest.fixture
def course_dir(root: Path) -> Path:
    d = root / "Example Course"
    (d / "Klausur" / "Altklausuren").mkdir(parents=True)
    return d


def test_suggests_by_folder_name(course_dir: Path) -> None:
    files = [
        remote("Homework 1.pdf", folder="Homework/Assignments"),
        remote("0 Intro.zip", folder="Lecture Slides"),
        remote("lecture-01.pdf", folder="Vorlesung"),
        remote("blatt1.pdf", folder="Übungsblätter"),
        remote("orga.pdf", folder="Allgemeines"),
    ]
    got = {s.rule["match"]["folder"]: s.rule["target"] for s in suggest_rules(files, course_dir)}
    assert got == {
        "Homework*": "Uebungen",
        "Lecture Slides*": "Skript : Alle Folien",
        "Vorlesung*": "Skript : Alle Folien",
        "Übungsblätter*": "Uebungen",
    }


def test_root_files_get_name_rules_and_nested_targets(course_dir: Path) -> None:
    files = [remote("Altklausur_2024.pdf"), remote("Klausur 2023.pdf"), remote("misc.pdf")]
    [s] = suggest_rules(files, course_dir)
    assert s.rule["target"] == "Klausur/Altklausuren"
    assert s.rule["match"]["folder"] == ""
    assert s.files == 2


def test_no_suggestion_without_matching_local_folder(tmp_path: Path) -> None:
    (tmp_path / "Course").mkdir()
    assert suggest_rules([remote("x.pdf", folder="Folien")], tmp_path / "Course") == []


def test_ids_are_unique(course_dir: Path) -> None:
    files = [remote("a.pdf", folder="Folien"), remote("b.pdf", folder="Folien!")]
    ids = [s.rule["id"] for s in suggest_rules(files, course_dir, {"slides-folien"})]
    assert len(ids) == len(set(ids)) and "slides-folien" not in ids


def test_append_rule_blocks_roundtrip(tmp_path: Path, root: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    original = (
        "# my comment\n[server]\nbase_url = 'https://studip.example.edu'\nusername = 'jdoe'\n"
        f"[sync]\nroot = '{root}'\n[courses.{COURSE_ID}]\ntarget = 'Example Course'\n"
        "[courses.other]\nexclude = true\n"
    )
    cfg_file.write_text(original)
    rule = {
        "id": "sheets",
        "match": {"folder": "Übung*", "name_regex": r"blatt (\d+)"},
        "target": "Uebungen",
        "rename": "Uebung_{1:02}{ext}",
    }
    backup = append_blocks(cfg_file, [rule_block(COURSE_ID, rule, "suggested")], "added")
    assert backup.read_text() == original
    assert cfg_file.read_text().startswith("# my comment")
    cfg = load(cfg_file)
    assert cfg.courses[COURSE_ID].rules[0].id == "sheets"
    assert cfg.courses[COURSE_ID].rules[0].match.name_regex.pattern == r"blatt (\d+)"


def test_append_refuses_to_break_config(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[courses.x]\nrules = 1\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        append_blocks(cfg_file, [rule_block("x", {"match": {}, "target": "a"})], "h")
    assert cfg_file.read_text() == "[courses.x]\nrules = 1\n"
    assert not (tmp_path / "config.toml.bak").exists()


def test_session_folders_collapse_into_one_pattern(course_dir: Path) -> None:
    files = [
        remote("s1.pdf", folder="01. Lab-Session am Mo, 02.11.30, 10:00 (Intro)"),
        remote("s2.pdf", folder="02. Lab-Session am Mo, 09.11.30, 10:00 (Vorlesung 2)"),
    ]
    [s] = suggest_rules(files, course_dir)
    assert s.rule["match"]["folder"] == "*Lab-Session*"
    assert s.files == 2 and "+1 more" in s.reason


def test_folder_pattern_keeps_plain_names() -> None:
    from studip_router.suggest import folder_pattern

    assert folder_pattern("Folien") == "Folien*"
    assert folder_pattern("Lecture Slides") == "Lecture Slides*"
    assert folder_pattern("3) Übungen") == "*Übungen*"


@pytest.mark.parametrize(
    ("local", "remote_folder", "expected"),
    [
        (["Übungen"], "Übungsblätter", "Übungen"),
        (["Exercises"], "Homework", "Exercises"),
        (["Hausaufgaben"], "Assignments", "Hausaufgaben"),
        (["Tutorien"], "Tutorium Mo 10-12", "Tutorien"),
        (["Vorlesungsfolien"], "Slides", "Vorlesungsfolien"),
        (["Lecture Notes"], "Folien", "Lecture Notes"),
        (["Exams", "Exams/Past Papers"], "Altklausuren", "Exams/Past Papers"),
        (["Prüfung"], "Klausurvorbereitung", "Prüfung"),
        (["Uebungen", "Uebungen/Loesungen"], "Musterlösungen", "Uebungen/Loesungen"),
        (["Uebungen"], "Lösungen", "Uebungen"),  # no solutions folder: with exercises
    ],
)
def test_generic_folder_names(tmp_path: Path, local, remote_folder, expected) -> None:
    course = tmp_path / "Course"
    for d in local:
        (course / d).mkdir(parents=True)
    [s] = suggest_rules([remote("x.pdf", folder=remote_folder)], course)
    assert s.rule["target"] == expected


def test_archive_folders_are_never_targets(tmp_path: Path) -> None:
    course = tmp_path / "Course"
    for d in ("Sonstiges/old/Übung", "Archiv/Folien"):
        (course / d).mkdir(parents=True)
    assert (
        suggest_rules([remote("x.pdf", folder="Übungen"), remote("y.pdf", folder="Folien")], course)
        == []
    )


def test_nested_remote_folders(tmp_path: Path) -> None:
    course = tmp_path / "Course"
    (course / "Folien").mkdir(parents=True)
    [s] = suggest_rules([remote("x.pdf", folder="Material/Vorlesungsfolien/Teil 1")], course)
    assert s.rule["match"]["folder"] == "Material/Vorlesungsfolien*"
