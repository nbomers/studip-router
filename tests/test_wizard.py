from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from studip_router.config import parse
from studip_router.wizard import CourseChoice, propose_target, render_config

FOLDERS = ["Quantum Cooking", "Advanced Knitting Theory", "Seminar", "Logic", "Rocket Gardening"]


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Introduction to Quantum Cooking (V)", "Quantum Cooking"),
        ("Advanced Kniting Theory", "Advanced Knitting Theory"),  # typo tolerated
        ("Seminar Generative Teapots", "Seminar"),
        ("Logic for Computer Scientists", "Logic"),
        ("Underwater Basket Weaving", None),
    ],
)
def test_propose_target(title: str, expected: str | None) -> None:
    assert propose_target(title, FOLDERS) == expected


def test_render_config_is_valid_and_quotes_safely() -> None:
    text = render_config(
        "https://studip.example.edu",
        "jdoe",
        "~/Uni/Semester 1",
        [
            CourseChoice("a" * 32, 'Course "One"', "WiSe 2030/31", "Course One"),
            CourseChoice("b" * 32, "Other", "WiSe 2030/31", None),
        ],
    )
    cfg = parse(tomllib.loads(text), Path("c.toml"))
    assert cfg.courses["a" * 32].name == 'Course "One"'
    assert cfg.courses["b" * 32].exclude


def test_detect_default_target(tmp_path: Path) -> None:
    from studip_router.wizard import detect_default_target

    dirs = []
    for course, misc in (("A", "Misc"), ("B", "Misc"), ("C", "Other stuff"), ("D", None)):
        d = tmp_path / course
        d.mkdir()
        (d / "Slides").mkdir()
        if misc:
            (d / misc).mkdir()
        dirs.append(d)
    assert detect_default_target(dirs) == "Misc"
    assert detect_default_target([dirs[3]]) is None


# -- finding the course folders and their structure ------------------------------------

TITLES = ["Introduction to Quantum Cooking", "Advanced Knitting Theory", "Rocket Gardening"]


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    return home


def test_finds_the_folder_with_most_courses(tmp_path: Path) -> None:
    from studip_router.wizard import find_roots

    home = _home(tmp_path)
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    semester = icloud / "Studium" / "Semester 5"
    for course in ("Quantum Cooking", "Knitting Theory", "Old stuff"):
        (semester / course).mkdir(parents=True)
    (icloud / "Studium" / "Semester 4" / "Quantum Cooking").mkdir(parents=True)
    (home / "Documents" / "Uni").mkdir()
    (home / "Library" / "Caches" / "Quantum Cooking").mkdir(parents=True)  # never searched
    roots = find_roots(TITLES, home)
    assert roots[0].path == semester and roots[0].matches == 2
    assert roots[0].label(home, 3) == "iCloud Drive/Studium/Semester 5  (2 of 3 courses found)"
    assert home / "Documents" / "Uni" in [r.path for r in roots]  # name hint, no courses yet


def test_new_root_prefers_icloud_then_documents(tmp_path: Path) -> None:
    from studip_router.wizard import new_root

    home = _home(tmp_path)
    assert new_root("WiSe 2026/27", home) == home / "Documents" / "Uni" / "WiSe 2026-27"
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    icloud.mkdir(parents=True)
    assert new_root("WiSe 2026/27", home) == icloud / "Uni" / "WiSe 2026-27"


def test_detect_course_folders_takes_the_common_structure(tmp_path: Path) -> None:
    from studip_router.wizard import detect_course_folders

    dirs = []
    for course, subs in (
        ("A", ("Klausur", "Skript : Alle Folien", "Sonstiges", "Uebungen")),
        ("B", ("Klausur", "Skript : Alle Folien", "Sonstiges", "Uebungen", "Extra")),
        ("C", ("Skript : Alle Folien", "Uebungen")),
    ):
        for sub in subs:
            (tmp_path / course / sub).mkdir(parents=True)
        dirs.append(tmp_path / course)
    assert detect_course_folders(dirs) == (
        "Klausur",
        "Skript : Alle Folien",
        "Sonstiges",
        "Uebungen",
    )
    assert detect_course_folders([tmp_path / "missing"]) is None


def test_create_course_folder_never_touches_existing_ones(tmp_path: Path) -> None:
    from studip_router.wizard import create_course_folder

    create_course_folder(tmp_path, "New", ("Folien", "Übungen"))
    assert sorted(p.name for p in (tmp_path / "New").iterdir()) == ["Folien", "Übungen"]
    (tmp_path / "Old").mkdir()
    create_course_folder(tmp_path, "Old", ("Folien",))
    assert list((tmp_path / "Old").iterdir()) == []


def test_config_keeps_course_folders(tmp_path: Path) -> None:
    text = render_config(
        "https://studip.example.edu", "jdoe", "~/Uni", [], "Sonstiges", ("Folien", "Übungen")
    )
    assert parse(tomllib.loads(text), Path("c.toml")).course_folders == ("Folien", "Übungen")
