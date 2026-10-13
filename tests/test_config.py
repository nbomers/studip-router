from __future__ import annotations

from pathlib import Path

import pytest

from studip_sync.config import ConfigError, load, parse

BASE = {
    "server": {"base_url": "https://studip.example.edu", "username": "jdoe"},
    "sync": {"root": "~/Uni"},
}


def _problems(raw: dict) -> list[str]:
    with pytest.raises(ConfigError) as info:
        parse(raw, Path("config.toml"))
    return info.value.problems


def test_minimal_config_uses_defaults() -> None:
    cfg = parse(BASE, Path("config.toml"))
    assert cfg.root == Path("~/Uni").expanduser()
    assert cfg.on_update == "version"
    assert cfg.default_target == "Sonstiges"
    assert cfg.exclude.mime == ("video/*",)
    assert cfg.exclude.max_size_mb == 200


def test_unknown_key_reports_path() -> None:
    raw = {**BASE, "sync": {"root": "~/Uni", "on_updte": "skip"}}
    assert any("sync.on_updte: unknown key" in p for p in _problems(raw))


def test_invalid_choice_and_type() -> None:
    raw = {**BASE, "sync": {"root": "~/Uni", "on_update": "nope", "create_missing_dirs": "yes"}}
    problems = _problems(raw)
    assert any("sync.on_update: must be one of" in p for p in problems)
    assert any("sync.create_missing_dirs: expected bool" in p for p in problems)


def test_course_requires_target_unless_excluded() -> None:
    raw = {**BASE, "courses": {"abc": {"name": "X"}, "def": {"exclude": True}}}
    problems = _problems(raw)
    assert problems == ["courses.abc.target: required"]


def test_parent_dir_traversal_rejected() -> None:
    raw = {**BASE, "courses": {"abc": {"target": "x", "default_target": "../escape"}}}
    assert any("'..' is not allowed" in p for p in _problems(raw))


def test_targets_are_nfc_normalised() -> None:
    nfd = "Übungen"  # "Übungen" decomposed, as macOS sometimes stores it
    raw = {**BASE, "courses": {"abc": {"target": nfd}}}
    assert parse(raw, Path("c.toml")).courses["abc"].target == "Übungen"


def test_course_exclusions_extend_global(tmp_path: Path) -> None:
    raw = {
        **BASE,
        "sync": {"root": "~/Uni", "exclude": {"globs": ["*.mp4"]}},
        "courses": {
            "abc": {"target": "x", "exclude_files": {"globs": ["*.zip"], "max_size_mb": 500}}
        },
    }
    cfg = parse(raw, Path("c.toml"))
    merged = cfg.course_exclude(cfg.courses["abc"])
    assert merged.globs == ("*.mp4", "*.zip")
    assert merged.max_size_mb == 500


def test_toml_syntax_error_has_line(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[server]\nbase_url = "https://x"\nusername = \n')
    with pytest.raises(ConfigError) as info:
        load(path)
    assert "line 3" in info.value.problems[0]


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="file not found"):
        load(tmp_path / "nope.toml")
