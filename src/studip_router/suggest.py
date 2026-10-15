"""Suggest routing rules from Stud.IP folder names and existing local folders."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fsutil import is_ignored, nfc
from .models import RemoteFile

# Keywords are matched against *normalised* names (lower case, umlauts as ae/oe/ue/ss),
# so "Übungen", "Uebungen", "UEBUNG" and "übungsblätter" all hit "uebung".
# Order matters: the first category whose keyword matches wins.
REMOTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exams": ("altklausur", "probeklausur", "klausur", "exam", "pruefung", "past paper", "testat"),
    "solutions": ("loesung", "solution", "answer"),
    "exercises": (
        "uebung",
        "ubung",
        "exercise",
        "blatt",
        "blaetter",
        "sheet",
        "homework",
        "hausaufgabe",
        "assignment",
        "aufgabe",
        "tutori",
        "praktikum",
        "problem set",
        "worksheet",
        "zettel",
        "abgabe",
        "lab",
    ),
    "slides": (
        "folie",
        "slide",
        "vorlesung",
        "lecture",
        "skript",
        "script",
        "praesentation",
        "presentation",
        "handout",
        "kapitel",
        "chapter",
        "vl",
    ),
}
LOCAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exams": ("klausur", "exam", "pruefung"),
    "solutions": ("loesung", "solution"),
    "exercises": (
        "uebung",
        "ubung",
        "exercise",
        "blatt",
        "blaetter",
        "sheet",
        "homework",
        "hausaufgabe",
        "assignment",
        "aufgabe",
        "tutori",
        "praktikum",
        "lab",
    ),
    "slides": ("skript", "script", "folie", "slide", "vorlesung", "lecture", "notes", "unterlagen"),
    "misc": ("sonstig", "misc", "other", "allgemein", "general", "divers", "inbox", "extra"),
}
# Local folders that are archives, never a target for new files.
_ARCHIVE = ("old", "alt", "archiv", "archive", "backup", "previous")
_PAST = ("alt", "old", "past", "previous", "frueher")


def normalise_name(text: str) -> str:
    text = nfc(text).casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        text = text.replace(a, b)
    return text


def _has_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    for kw in keywords:
        if len(kw) <= 3:  # short ones ("vl", "lab", "alt", "old") only as whole words
            if re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", text):
                return True
        elif kw in text:
            return True
    return False


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
    words = normalise_name(text)
    for name, keywords in REMOTE_KEYWORDS.items():
        if _has_keyword(words, keywords):
            return name
    return None


def folder_category(folder_path: str) -> str | None:
    """Category of the first of the top three Stud.IP folders whose name reveals one."""
    for part in [p for p in folder_path.split("/") if p][:3]:
        if (category := _category(part)) is not None:
            return category
    return None


def auto_target(folder_path: str, subdirs: list[str]) -> tuple[str, str] | None:
    """(category, local folder) for a file without a matching rule, if both are clear."""
    category = folder_category(folder_path)
    if category is None:
        return None
    target = _local_target(category, subdirs)
    return (category, target) if target is not None else None


def local_category_dirs(subdirs: list[str], category: str) -> list[str]:
    """Local folders for ``category``, best first; archive folders are never offered."""
    keywords = LOCAL_KEYWORDS[category]
    scored: list[tuple[int, str]] = []
    for rel in subdirs:
        parts = [normalise_name(p) for p in rel.split("/")]
        if any(_has_keyword(p, _ARCHIVE) for p in parts):
            continue
        if not _has_keyword(parts[-1], keywords) and not (
            category == "exams"
            and len(parts) > 1
            and _has_keyword(parts[0], keywords)
            and _has_keyword(parts[-1], (*_PAST, "altklausur"))
        ):
            continue
        score = 10 - len(parts)
        if category == "exams" and _has_keyword(parts[-1], (*_PAST, "altklausur")):
            score += 5  # old exams go into "Klausur/Altklausuren" rather than "Klausur"
        scored.append((score, rel))
    return [rel for _, rel in sorted(scored, key=lambda x: (-x[0], x[1]))]


def _local_target(category: str, subdirs: list[str]) -> str | None:
    found = local_category_dirs(subdirs, category)
    if found:
        return found[0]
    if category == "solutions":  # no own folder: solutions go with the exercises
        return _local_target("exercises", subdirs)
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
    suggestions: list[Suggestion] = []

    # Each file is classified by the first folder on its path (up to 3 levels)
    # whose name reveals a category: "Material/Folien/x.pdf" -> "Material/Folien".
    by_prefix: Counter[str] = Counter()
    for f in files:
        parts = [p for p in f.folder_path.split("/") if p][:3]
        for depth in range(1, len(parts) + 1):
            if _category(parts[depth - 1]) is not None:
                by_prefix["/".join(parts[:depth])] += 1
                break
        else:
            if parts:  # may still join a classified series of session folders
                by_prefix[parts[0]] += 1

    # Per-session folders ("01. Sitzung am 04.11.30 ...") collapse into one pattern.
    members: dict[str, list[str]] = {}
    for prefix in sorted(by_prefix):
        parent, _, last = prefix.rpartition("/")
        pattern = (parent + "/" if parent else "") + folder_pattern(last)
        members.setdefault(pattern, []).append(prefix)
    grouped: dict[str, tuple[str, int, list[str]]] = {}
    for pattern, names in members.items():
        # one session folder named "... Vorlesung" classifies the whole series
        category = next(
            (c for n in names if (c := _category(n.rpartition("/")[2])) is not None), None
        )
        if category is not None:
            grouped[pattern] = (category, sum(by_prefix[n] for n in names), names)

    for pattern, (category, count, names) in grouped.items():
        target = _local_target(category, subdirs)
        if target is None:
            continue
        rule_id = _unique(f"{category}-{_slug(pattern.strip('*').rpartition('/')[2])}", taken)
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
    keywords = REMOTE_KEYWORDS
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

    "03. Sitzung am Mi, 04.11.30 (...)" -> "*Sitzung*",
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
