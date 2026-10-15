from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import COURSE_ID, make_config, remote

from studip_router.config import ConfigError, parse
from studip_router.planner import Kind, SkipReason, build_router, exclusion_reason, plan_course
from studip_router.rename import RenameError, RenameOptions, normalise, target_name
from studip_router.rules import parse_rules


def _cfg(root: Path, rules: list[dict], global_rename: dict | None = None, **course):
    return parse(
        {
            "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
            "sync": {"root": str(root)},
            "rename": global_rename or {},
            "courses": {
                COURSE_ID: {"name": "Example", "target": "Example Course", "rules": rules, **course}
            },
        },
        Path("config.toml"),
    )


RULES = [
    {
        "id": "sheets",
        "match": {"folder": "Übungsblätter*", "name_regex": r"blatt[_ ]?(\d+)"},
        "target": "Uebungen",
        "rename": "Uebung_{1:02}{ext}",
    },
    {
        "id": "slides",
        "match": {"folder": "Folien*", "ext": "pdf"},
        "target": "Skript : Alle Folien",
    },
    {"id": "exams", "match": {"name_regex": "klausur|exam"}, "target": "Klausur/Altklausuren"},
    {"id": "no-videos", "match": {"mime": "video/*"}, "skip": True},
]


# -- rename ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "options", "expected"),
    [
        ("lecture-1.pdf", RenameOptions(), "lecture-01.pdf"),
        ("lecture-12.pdf", RenameOptions(), "lecture-12.pdf"),
        ("WS2026_VL_3.pdf", RenameOptions(), "WS2026_VL_03.pdf"),
        ("lecture-1.pdf", RenameOptions(pad_numbers=0), "lecture-1.pdf"),
        (
            "Übung 3 Lösung.pdf",
            RenameOptions(umlauts="transliterate", spaces="_"),
            "Uebung_03_Loesung.pdf",
        ),
        (
            "Vorlesung_Intro.PDF",
            RenameOptions(strip_prefixes=("vorlesung_",), lowercase=True),
            "intro.pdf",
        ),
        ("Kapitel 1.v2.pdf", RenameOptions(), "Kapitel 01.v2.pdf"),
        ("H2O Python3 IPv6 A4 2x2.pdf", RenameOptions(), "H2O Python3 IPv6 A4 2x2.pdf"),
    ],
)
def test_normalise(name: str, options: RenameOptions, expected: str) -> None:
    assert normalise(name, options) == expected


def test_nfd_input_becomes_nfc() -> None:
    assert normalise("Übung.pdf", RenameOptions()) == "Übung.pdf"


def test_template_variables_and_groups() -> None:
    import re

    file = remote(
        "Blatt7.pdf", folder="Übungsblätter/Woche 3", chdate=datetime(2026, 11, 2, tzinfo=UTC)
    )
    m = re.search(r"Blatt(?P<num>\d+)", file.name)
    name = target_name(
        file,
        "{course}_{folder}_{num:03}_{date}{ext}",
        RenameOptions(pad_numbers=0),
        course="DRL",
        match=m,
    )
    assert name == "DRL_Woche 3_007_2026-11-02.pdf"


def test_template_producing_path_is_rejected() -> None:
    with pytest.raises(RenameError, match="produced a path"):
        target_name(remote("a.pdf", folder="x/y"), "{folder_path}-{orig_name}", RenameOptions())


def test_unknown_variable_at_render_time() -> None:
    with pytest.raises(RenameError, match="unknown template variable"):
        target_name(remote("a.pdf"), "{nope}{ext}", RenameOptions())


# -- rule parsing ---------------------------------------------------------------


def test_rule_errors_have_paths(root: Path) -> None:
    bad = [
        {"match": {"name_regex": "(unclosed"}, "target": "x"},
        {"match": {"name_glob": "*.pdf", "colour": "red"}, "target": "../out"},
        {"match": {}, "target": "x", "rename": "Uebung_{1:02}{ext}"},
        {"id": "dup", "match": {}, "target": "a"},
        {"id": "dup", "match": {}},
    ]
    with pytest.raises(ConfigError) as info:
        _cfg(root, bad)
    text = "\n".join(info.value.problems)
    rules = f"courses.{COURSE_ID}.rules"
    assert f"{rules}[0].match.name_regex: invalid regex" in text
    assert f"{rules}[1].match.colour: unknown key" in text
    assert f"{rules}[1].target: must be a relative path" in text
    assert f"{rules}[2].rename: unknown template variable {{1}}" in text
    assert f"{rules}[4].id: duplicate id 'dup'" in text
    assert f"{rules}[4].target: required" in text


def test_dates_and_sizes_parse() -> None:
    rules, problems = parse_rules(
        [{"match": {"changed_after": "2026-10-01", "max_size_mb": 5}, "target": "x"}], "r"
    )
    assert problems == []
    assert rules[0].match.changed_after.isoformat() == "2026-10-01"


# -- routing --------------------------------------------------------------------


def test_first_matching_rule_wins(root: Path) -> None:
    route = build_router(_cfg(root, RULES), _cfg(root, RULES).courses[COURSE_ID])
    r = route(remote("Blatt_3.pdf", folder="Übungsblätter"))
    assert (r.subdir, r.name, r.rule_id) == ("Uebungen", "Uebung_03.pdf", "sheets")
    r = route(remote("Klausur 2024.pdf", folder="Folien"))
    assert r.rule_id == "slides"  # slides rule comes before exams
    r = route(remote("Altklausur.pdf", folder="Sonstiges"))
    assert r.subdir == "Klausur/Altklausuren"
    r = route(remote("notes.txt", folder="Misc"))
    assert r.by_default and r.subdir == "Sonstiges"


def test_folder_glob_is_case_insensitive_and_unicode_safe(root: Path) -> None:
    cfg = _cfg(root, RULES)
    route = build_router(cfg, cfg.courses[COURSE_ID])
    # NFD folder name from the API and different case
    r = route(remote("blatt1.pdf", folder="ÜBUNGSBLÄTTER"))
    assert r.rule_id == "sheets"


def test_skip_rule(root: Path) -> None:
    raw = {
        "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
        "sync": {"root": str(root), "exclude": {"mime": []}},
        "courses": {COURSE_ID: {"target": "Example Course", "rules": RULES}},
    }
    cfg = parse(raw, Path("c.toml"))
    plan = plan_course(cfg, cfg.courses[COURSE_ID], [remote("vl.mp4", mime="video/mp4")], {})
    assert plan.actions[0].kind is Kind.SKIP
    assert "no-videos" in plan.actions[0].reason


def test_renamed_target_still_adopts_hand_download(root: Path, state) -> None:
    (root / "Example Course" / "Uebungen" / "Blatt_3.pdf").write_bytes(b"abc")
    cfg = _cfg(root, RULES)
    plan = plan_course(
        cfg, cfg.courses[COURSE_ID], [remote("Blatt 3.pdf", folder="Übungsblätter")], {}
    )
    assert [a.kind for a in plan.actions] == [Kind.ADOPT]


def test_two_files_renamed_to_same_name_conflict(root: Path, state) -> None:
    rules = [{"match": {"name_regex": r"(\d+)"}, "target": "Uebungen", "rename": "Blatt_{1}{ext}"}]
    cfg = _cfg(root, rules)
    files = [remote("sheet 1.pdf", id="a"), remote("blatt-1.pdf", id="b")]
    plan = plan_course(cfg, cfg.courses[COURSE_ID], files, {})
    assert sorted(a.dest.name for a in plan.actions) == ["Blatt_01.pdf", "Blatt_01_2.pdf"]


def test_per_course_rename_overrides_global(root: Path) -> None:
    cfg = _cfg(root, [], global_rename={"pad_numbers": 3}, rename={"spaces": "_"})
    route = build_router(cfg, cfg.courses[COURSE_ID])
    assert route(remote("VL 1.pdf")).name == "VL_001.pdf"


def test_folder_type_rule(root: Path) -> None:
    from dataclasses import replace

    rules = [{"id": "mine", "match": {"folder_type": "HomeworkFolder"}, "skip": True}]
    cfg = _cfg(root, rules)
    route = build_router(cfg, cfg.courses[COURSE_ID])
    assert route(replace(remote("my.pdf"), folder_type="HomeworkFolder")).skip
    assert not route(replace(remote("x.pdf"), folder_type="StandardFolder")).skip


# -- automatic routing by Stud.IP folder name ------------------------------------------


def _auto_cfg(root: Path, **sync: object):
    for sub in ("Uebungen", "Skript : Alle Folien", "Klausur", "Sonstiges"):
        (root / "Example Course" / sub).mkdir(parents=True, exist_ok=True)
    return make_config(root, **sync)


@pytest.mark.parametrize(
    ("folder", "target"),
    [
        ("Slides", "Skript : Alle Folien"),
        ("Vorlesungsfolien", "Skript : Alle Folien"),
        ("Unterlagen zur Übung", "Uebungen"),
        ("Tutorium MI 16-18 Uhr", "Uebungen"),
        ("Material/Übungsblätter", "Uebungen"),
        ("Loesung", "Uebungen"),  # no own solutions folder: with the exercises
        ("Tag 1", "Sonstiges"),  # unclear: default folder, never a guess
        ("", "Sonstiges"),
    ],
)
def test_files_without_rule_follow_the_folder_name(
    tmp_path: Path, folder: str, target: str
) -> None:
    cfg = _auto_cfg(tmp_path)
    route = build_router(cfg, cfg.courses[COURSE_ID])(remote("x.pdf", folder=folder))
    assert route.subdir == target
    assert route.by_default == (target == "Sonstiges")


def test_rules_win_over_automatic_routing(tmp_path: Path) -> None:
    cfg = _auto_cfg(tmp_path)
    rules, problems = parse_rules(
        [{"id": "all", "match": {"folder": "Slides"}, "target": "Klausur"}], "rules"
    )
    assert problems == []
    course = replace(cfg.courses[COURSE_ID], rules=rules)
    assert build_router(cfg, course)(remote("x.pdf", folder="Slides")).rule_id == "all"


def test_automatic_routing_can_be_turned_off(tmp_path: Path) -> None:
    cfg = _auto_cfg(tmp_path, auto_route=False)
    route = build_router(cfg, cfg.courses[COURSE_ID])(remote("x.pdf", folder="Slides"))
    assert route.subdir == "Sonstiges" and route.by_default


def test_courseware_import_folders_are_skipped_by_default(tmp_path: Path) -> None:
    cfg = _auto_cfg(tmp_path)
    excludes = cfg.course_exclude(cfg.courses[COURSE_ID])
    junk = remote("blob[131]", folder="CoursewareImport 14.10.2026, 07:58:45 458/Unknown")
    assert exclusion_reason(junk, excludes) is SkipReason.EXCLUDED
    assert exclusion_reason(remote("x.pdf", folder="Folien"), excludes) is None
    # and it can be switched off
    cfg = make_config(tmp_path, exclude={"folders": []})
    assert exclusion_reason(junk, cfg.course_exclude(cfg.courses[COURSE_ID])) is None
