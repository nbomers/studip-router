"""Suggest routing rules from Stud.IP folder names and existing local folders."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fsutil import is_ignored, nfc
from .models import RemoteFile

# (category, keywords found in Stud.IP folder/file names, preferred local folders)
CATEGORIES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (
        "exams",
        ("altklausur", "klausur", "exam", "prüfung", "pruefung"),
        ("klausur/altklausuren", "klausur", "exams", "pruefung", "prüfung"),
    ),
    (
        "exercises",
        (
            "übung",
            "uebung",
            "blatt",
            "sheet",
            "homework",
            "assignment",
            "exercise",
            "aufgabe",
            "tutori",
            "praktikum",
            "lab",
        ),
        ("uebungen", "übungen", "uebung", "übung", "exercises", "homework", "tutorium"),
    ),
    (
        "slides",
        ("folie", "slide", "vorlesung", "lecture", "skript", "script", "vl"),
        ("skript : alle folien", "skript", "folien", "slides", "vorlesung", "lecture"),
    ),
]


@dataclass(frozen=True)
class Suggestion:
    rule: dict[str, Any]
    category: str
    files: int
    reason: str


def local_subdirs(course_dir: Path, depth: int = 2) -> list[str]:
    """Relative POSIX paths of subfolders, NFC, without hidden or ignored ones."""
    result: list[str] = []

    def walk(directory: Path, prefix: str, level: int) -> None:
        try:
            entries = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for entry in entries:
            if entry.name.startswith(".") or is_ignored(entry.name):
                continue
            rel = f"{prefix}{nfc(entry.name)}"
            result.append(rel)
            if level < depth:
                walk(entry, rel + "/", level + 1)

    walk(course_dir, "", 1)
    return result


def _category(text: str) -> str | None:
    words = nfc(text).casefold()
    for name, keywords, _ in CATEGORIES:
        for kw in keywords:
            # "vl" and "lab" only as whole words, the rest as substrings
            if len(kw) <= 3:
                if re.search(rf"(?<![a-zäöü]){re.escape(kw)}(?![a-zäöü])", words):
                    return name
            elif kw in words:
                return name
    return None


def _local_target(category: str, subdirs: list[str]) -> str | None:
    by_key = {d.casefold(): d for d in subdirs}
    preferred = next(c for name, _, c in CATEGORIES if name == category)
    for candidate in preferred:
        if candidate in by_key:
            return by_key[candidate]
    return None


def _slug(text: str) -> str:
    slug = re.sub(
        r"[^a-z0-9]+",
        "-",
        nfc(text)
        .casefold()
        .replace("ü", "ue")
        .replace("ö", "oe")
        .replace("ä", "ae")
        .replace("ß", "ss"),
    )
    return slug.strip("-")[:30] or "root"


def suggest_rules(
    files: list[RemoteFile], course_dir: Path, existing_ids: set[str] | None = None
) -> list[Suggestion]:
    """One rule per top-level Stud.IP folder whose name reveals its category.

    Files in the course root are classified by file name instead, so that e.g.
    "Altklausur_2024.pdf" gets its own name-based rule.
    """
    subdirs = local_subdirs(course_dir)
    taken = set(existing_ids or ())
    by_folder: Counter[str] = Counter(f.folder_path.split("/", 1)[0] for f in files)
    suggestions: list[Suggestion] = []

    for top, count in sorted(by_folder.items()):
        if not top:
            continue
        category = _category(top)
        if category is None:
            continue
        target = _local_target(category, subdirs)
        if target is None:
            continue
        rule_id = _unique(f"{category}-{_slug(top)}", taken)
        suggestions.append(
            Suggestion(
                rule={"id": rule_id, "match": {"folder": f"{top}*"}, "target": target},
                category=category,
                files=count,
                reason=f"Stud.IP folder {top!r} looks like {category}",
            )
        )

    root_files = [f for f in files if not f.folder_path]
    by_category: dict[str, int] = Counter(
        c for f in root_files if (c := _category(f.name)) is not None
    )
    keywords = {name: kws for name, kws, _ in CATEGORIES}
    for category, count in sorted(by_category.items()):
        target = _local_target(category, subdirs)
        if target is None:
            continue
        pattern = "|".join(re.escape(k) for k in keywords[category] if len(k) > 3)
        rule_id = _unique(f"{category}-by-name", taken)
        suggestions.append(
            Suggestion(
                rule={
                    "id": rule_id,
                    "match": {"folder": "", "name_regex": pattern},
                    "target": target,
                },
                category=category,
                files=count,
                reason=f"{count} file(s) in the course root are named like {category}",
            )
        )
    return suggestions


def _unique(rule_id: str, taken: set[str]) -> str:
    candidate, n = rule_id, 2
    while candidate in taken:
        candidate = f"{rule_id}-{n}"
        n += 1
    taken.add(candidate)
    return candidate
