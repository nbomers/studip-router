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
