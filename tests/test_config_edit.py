from __future__ import annotations

from pathlib import Path

import pytest

from studip_router.config import ConfigError
from studip_router.config_edit import get_value, set_course, set_value

ORIGINAL = """# my settings, keep this comment
[server]
base_url = "https://studip.example.edu"  # inline comment
username = "jdoe"

[sync]
root = "~/Uni"
on_update = "version"

[courses.abc]
name = "A"   # course A
target = "A"
"""


@pytest.fixture
def cfg_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(ORIGINAL)
    return path


def test_set_value_keeps_comments_and_makes_backup(cfg_file: Path) -> None:
    cfg = set_value(cfg_file, ("sync", "on_update"), "skip")
    assert cfg.on_update == "skip"
    text = cfg_file.read_text()
    assert "# my settings, keep this comment" in text and "# inline comment" in text
    assert (cfg_file.parent / "config.toml.bak").read_text() == ORIGINAL


def test_nested_tables_are_created(cfg_file: Path) -> None:
    set_value(cfg_file, ("sync", "exclude", "max_size_mb"), 500)
    set_value(cfg_file, ("ui", "theme"), "nord")
    cfg = set_value(cfg_file, ("rename", "pad_numbers"), 3)
    assert cfg.exclude.max_size_mb == 500 and cfg.ui_theme == "nord"
    assert cfg.rename.pad_numbers == 3
    assert get_value(cfg_file, ("sync", "exclude", "max_size_mb")) == 500


def test_invalid_value_is_never_written(cfg_file: Path) -> None:
    before = cfg_file.read_text()
    with pytest.raises(ConfigError):
        set_value(cfg_file, ("sync", "on_update"), "explode")
    assert cfg_file.read_text() == before


def test_remove_value(cfg_file: Path) -> None:
    cfg = set_value(cfg_file, ("sync", "on_update"), None)
    assert cfg.on_update == "version"  # back to default
    assert "on_update" not in cfg_file.read_text()


def test_set_course_toggle_and_add(cfg_file: Path) -> None:
    cfg = set_course(cfg_file, "abc", exclude=True)
    assert cfg.courses["abc"].exclude
    cfg = set_course(cfg_file, "abc", exclude=None)
    assert not cfg.courses["abc"].exclude
    cfg = set_course(cfg_file, "new1", name="New", target="New Folder")
    assert cfg.courses["new1"].target == "New Folder"
    assert "# course A" in cfg_file.read_text()
