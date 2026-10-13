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

    # Per-session folders ("01. Sitzung am 04.11.30 ...") collapse into one pattern.
    members: dict[str, list[str]] = {}
    for top in sorted(by_folder):
        if top:
            members.setdefault(folder_pattern(top), []).append(top)
    grouped: dict[str, tuple[str, int, list[str]]] = {}
    for pattern, names in members.items():
        # one session folder named "... Vorlesung" classifies the whole series
        category = next((c for n in names if (c := _category(n)) is not None), None)
        if category is not None:
            grouped[pattern] = (category, sum(by_folder[n] for n in names), names)

    for pattern, (category, count, names) in grouped.items():
        target = _local_target(category, subdirs)
        if target is None:
            continue
        rule_id = _unique(f"{category}-{_slug(pattern.strip('*'))}", taken)
        shown = names[0] if len(names) == 1 else f"{names[0]} (+{len(names) - 1} more)"
        suggestions.append(
            Suggestion(
                rule={"id": rule_id, "match": {"folder": pattern}, "target": target},
                category=category,
                files=count,
                reason=f"Stud.IP folder {shown!r} looks like {category}",
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


_NUMBERED = re.compile(r"^\s*(?:\d+|[IVX]+)[.):\-]?\s+")
_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.(?:\d{2,4})?|\d{4}-\d{2}-\d{2}")


def folder_pattern(top: str) -> str:
    """Glob for a top-level folder; numbered or dated session folders are generalised.

    "01. Lab-Session am Di, 04.11.30 (...)" -> "*Lab-Session*",
    "Folien" -> "Folien*".
    """
    if not (_NUMBERED.match(top) or _DATE.search(top)):
        return f"{top}*"
    rest = _NUMBERED.sub("", top)
    words = [w for w in re.split(r"[\s,()]+", rest) if len(w) >= 4 and not _DATE.search(w)]
    if not words:
        return f"{top}*"
    return f"*{words[0]}*"


def _unique(rule_id: str, taken: set[str]) -> str:
    candidate, n = rule_id, 2
    while candidate in taken:
        candidate = f"{rule_id}-{n}"
        n += 1
    taken.add(candidate)
    return candidate
