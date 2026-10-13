from pathlib import Path

from studip_router.config import load

EXAMPLE = Path(__file__).resolve().parent.parent / "config.example.toml"


def test_example_config_is_valid() -> None:
    cfg = load(EXAMPLE)
    course = cfg.courses["0123456789abcdef0123456789abcdef"]
    assert [r.id for r in course.rules] == [
        "sheets",
        "slides",
        "old-exams",
        "own-submissions",
        "no-recordings",
    ]
    assert course.merge is not None and course.unzip
    assert cfg.courses["fedcba9876543210fedcba9876543210"].exclude
