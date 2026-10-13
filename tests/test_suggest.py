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
