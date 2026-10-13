"""Helpers for `studip init`: match courses to existing folders, write a config."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .fsutil import nfc
from .tomlwrite import key, value

# Words that carry no meaning for matching a course title to a folder name.
STOPWORDS = {
    "einführung",
    "einfuehrung",
    "in",
    "die",
    "der",
    "das",
    "den",
    "und",
    "für",
    "fuer",
    "of",
    "to",
    "the",
    "and",
    "for",
    "introduction",
    "v",
    "ü",
    "ue",
    "vl",
    "vorlesung",
    "informatiker",
    "innen",
    "informatikerinnen",
    "a",
    "i",
    "ii",
}


def _tokens(text: str) -> list[str]:
    words = re.split(r"[^\wäöüß]+", nfc(text).casefold())
    return [w for w in words if w and w not in STOPWORDS]


def _token_match(a: str, b: str) -> bool:
    return a == b or (len(a) > 4 and len(b) > 4 and SequenceMatcher(None, a, b).ratio() > 0.85)


def score(title: str, folder: str) -> float:
    """Share of the folder's words found in the course title (fuzzy, typo tolerant)."""
    folder_tokens = _tokens(folder)
    title_tokens = _tokens(title)
    if not folder_tokens or not title_tokens:
        return 0.0
    hits = sum(1 for f in folder_tokens if any(_token_match(f, t) for t in title_tokens))
    return hits / len(folder_tokens)


def propose_target(title: str, folders: list[str]) -> str | None:
    best: tuple[float, float, str] | None = None
    for folder in folders:
        s = score(title, folder)
        if s < 0.6:
            continue
        tie = SequenceMatcher(None, nfc(title).casefold(), nfc(folder).casefold()).ratio()
        if best is None or (s, tie) > best[:2]:
            best = (s, tie, folder)
    return best[2] if best else None


def local_course_folders(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(nfc(p.name) for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


@dataclass(frozen=True)
class CourseChoice:
    id: str
    title: str
    semester: str
    target: str | None  # None: excluded


def detect_default_target(course_dirs: list[Path]) -> str | None:
    """The catch-all folder most course folders share ("Sonstiges", "Misc", ...)."""
    from collections import Counter

    from .suggest import local_category_dirs, local_subdirs

    found: Counter[str] = Counter()
    for course_dir in course_dirs:
        dirs = local_category_dirs(local_subdirs(course_dir, depth=1), "misc")
        if dirs:
            found[dirs[0]] += 1
    return found.most_common(1)[0][0] if found else None


def render_config(
    base_url: str,
    username: str,
    root: str,
    courses: list[CourseChoice],
    default_target: str = "Sonstiges",
) -> str:
    lines = [
        "# studip-router configuration - see config.example.toml for all options.",
        "# The password is stored in your OS keychain, never in this file.",
        "",
        "[server]",
        f"base_url = {value(base_url)}",
        f"username = {value(username)}",
        "",
        "[sync]",
        f"root = {value(root)}",
        'semester = "current"',
        f"default_target = {value(default_target)}  # where files go that no rule matches",
        "create_missing_dirs = false",
        "",
        "# Courses are keyed by their Stud.IP ID; `name` is only a label.",
        "# Add [[courses.<id>.rules]] blocks (or run `studip rules suggest`) to route files.",
    ]
    for c in courses:
        lines += ["", f"[courses.{key(c.id)}]", f"name = {value(c.title)}  # {c.semester}"]
        if c.target is None:
            lines.append("exclude = true")
        else:
            lines.append(f"target = {value(c.target)}")
    return "\n".join(lines) + "\n"
