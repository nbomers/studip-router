"""Suggest routing rules from Stud.IP folder names and existing local folders."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fsutil import is_ignored, nfc
from .models import RemoteFile

# Keywords are matched against *normalised* names (lower case, umlauts as ae/oe/ue/ss,
# "_", "-", "." and "+" as spaces), so "Übungen", "Uebungen", "UEBUNG", "übungs_blätter"
# and "Ubung" (no umlaut, no e) all hit. Keywords of up to three letters ("vl", "hw",
# "ue", "lab") only count as whole words, so "VL03" and "HW 2" hit but "Label" does not.
# Order matters: the first category whose keyword matches wins, so "Klausur Lösung"
# is an exam and "Lösung Blatt 3" a solution (they go with the exercises when there is
# no solutions folder).
#
# Sources for the German terms: lecture and exercise formats as Stud.IP courses at
# German universities name them (Vorlesung, Übung/Zentralübung/Präsenzübung/
# Hausübung, Tutorium, Praktikum, Testat, Semesterapparat, Lehrbrief/Kurseinheit at
# the FernUni); English: Moodle and course websites (lecture notes, problem sets,
# recitations, past papers, readings).
REMOTE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exams": (
        "klausur",  # also Altklausur, Probeklausur, Nachklausur, Klausurvorbereitung
        "pruefung",  # also Prüfungsvorbereitung; not Prüfungsordnung (see below)
        "prufung",  # "Prufung" written without the e
        "exam",  # also Exams, Past exams, Mock exam, Examination
        "past paper",
        "midterm",
        "testat",
    ),
    "solutions": (
        "loesung",  # also Musterlösung, Lösungsvorschlag, Lösungshinweise
        "losung",
        "solution",
        "answer",  # also Answer key, Answers
        "erwartungshorizont",
    ),
    "exercises": (
        "uebung",  # also Übungsblatt, Hausübung, Präsenzübung, Zentralübung, Globalübung
        "ubung",
        "ue",  # "Ü 3", "UE03"
        "exercise",
        "blatt",  # also Aufgabenblatt, Arbeitsblatt; not Merkblatt, Deckblatt (below)
        "blaetter",
        "zettel",  # also Übungszettel; not Laufzettel (below)
        "aufgabe",  # also Hausaufgabe, Einsendeaufgabe, Programmieraufgabe
        "abgabe",
        "homework",
        "hw",  # "HW1", "hw 3"
        "assignment",
        "sheet",  # also Exercise sheet, Worksheet; not Datasheet (below)
        "problem set",
        "problemset",
        "tutori",  # Tutorium, Tutorien, Tutorial
        "recitation",
        "praktikum",
        "labor",  # also Laborpraktikum, Laboratory
        "lab",
        "notebook",  # Jupyter notebooks ("Notebooks", "notebook-03") are worked on like sheets
        "workbook",
    ),
    "slides": (
        "folie",  # also Vorlesungsfolien, Foliensatz
        "slide",
        "vorlesung",  # also Vorlesungsunterlagen; not Vorlesungsverzeichnis (below)
        "lecture",  # also Lecture notes
        "vl",  # "VL 3", "VL03"
        "skript",  # also Skriptum, Vorlesungsskript
        "script",  # not "Scripts" (code, below)
        "praesentation",
        "prasentation",
        "presentation",
        "vortrag",
        "vortraege",
        "handout",
        "kapitel",
        "chapter",
        "lektion",
        "mitschrift",
        "tafel",  # Tafelbild, Tafelanschrieb
        "whiteboard",
        "zusammenfassung",
        "summary",
        "formelsammlung",
        "lehrbrief",  # FernUni: Lehrbrief, Studienbrief, Kurseinheit
        "studienbrief",
        "kurseinheit",
    ),
    # Only used when a local folder for it exists ("Literatur", "Reading", ...).
    "reading": (
        "literatur",  # also Literaturliste, Pflichtliteratur
        "lektuere",
        "lekture",
        "reader",
        "reading",
        "semesterapparat",
        "paper",  # seminar papers; "Past papers" are exams (checked first)
        "texte",
        "quellen",
        "bibliograph",
        "book",  # "Notebook" is an exercise (checked first)
        "buch",  # also Lehrbuch; not Handbuch (below)
        "artikel",
        "article",
    ),
}
LOCAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "exams": ("klausur", "exam", "pruefung", "prufung", "testat"),
    "solutions": ("loesung", "losung", "solution"),
    "exercises": (
        "uebung",
        "ubung",
        "exercise",
        "blatt",
        "blaetter",
        "zettel",
        "sheet",
        "homework",
        "hausaufgabe",
        "assignment",
        "aufgabe",
        "abgabe",
        "tutori",
        "praktikum",
        "lab",
        "labor",
        "problem set",
    ),
    "slides": (
        "skript",
        "script",
        "folie",
        "slide",
        "vorlesung",
        "lecture",
        "notes",
        "unterlagen",
        "mitschrift",
        "vl",
    ),
    "reading": (
        "literatur",
        "reading",
        "reader",
        "lektuere",
        "paper",
        "texte",
        "buecher",
        "books",
        "semesterapparat",
        "quellen",
    ),
    "misc": ("sonstig", "misc", "other", "allgemein", "general", "divers", "inbox", "extra"),
}
# Words that contain a keyword but mean something else. They are removed before the
# keywords are matched: "Examples" is no exam, "Cheat sheet" or "Datasheet" no exercise
# sheet, "Merkblatt"/"Deckblatt" no exercise, "Laufzettel" a form, a "Scripts"/"Code"
# folder holds programs, "Prüfungsordnung" and "Vorlesungsverzeichnis" are regulations
# and timetables, a "Handbuch" a manual.
_FALSE_FRIENDS = re.compile(
    r"example\w*|exemplar\w*"
    r"|cheat ?sheets?|data ?sheets?|spread ?sheets?|cover ?sheets?|info ?sheets?"
    r"|(?:merk|deck|info|daten|hinweis|bei|anmelde|formel)blae?tt\w*"
    r"|laufzettel\w*|stundenzettel\w*|(?:spick|notiz|hand)zettel\w*"
    r"|(?:titel|kalender)blae?tt\w*|sheet ?music"
    r"|(?:labor|praktikums|uebungs)ordnung\w*"
    r"|(?:klausur|pruefungs?)anmeldung\w*"
    r"|facebook\w*|wallpaper\w*"
    r"|(?<![a-z])scripts(?![a-z])|(?<![a-z])(?:source ?)?code(?![a-z])"
    r"|pruefungs?(?:ordnung|amt|ausschuss|recht)\w*"
    r"|vorlesungs(?:verzeichnis|frei|zeit|plan|beginn|ende)\w*"
    r"|handbuch\w*|logbuch\w*|buchung\w*"
)

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
            # whole word, plural too: "Labs", "HWs", "VLs"
            if re.search(rf"(?<![a-z]){re.escape(kw)}s?(?![a-z])", text):
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


Keywords = dict[str, tuple[str, ...]]


def _category(text: str, own: Keywords | None = None) -> str | None:
    words = re.sub(r"[_\-.+]+", " ", normalise_name(text))
    if own:  # your own words first ([sync.keywords] in the config): they say what you mean
        for name in REMOTE_KEYWORDS:
            if _has_keyword(words, own.get(name, ())):
                return name
    words = _FALSE_FRIENDS.sub(" ", words)
    for name, keywords in REMOTE_KEYWORDS.items():
        if _has_keyword(words, keywords):
            return name
    return None


def folder_category(folder_path: str, own: Keywords | None = None) -> str | None:
    """Category of the first of the top three Stud.IP folders whose name reveals one."""
    for part in [p for p in folder_path.split("/") if p][:3]:
        if (category := _category(part, own)) is not None:
            return category
    return None


def auto_target(folder_path: str, subdirs: list[str]) -> tuple[str, str] | None:
    """(category, local folder) for a file without a matching rule, if both are clear."""
    category = folder_category(folder_path)
    if category is None:
        return None
    target = _local_target(category, subdirs)
    return (category, target) if target is not None else None


# Stud.IP folder types whose purpose is fixed, whatever the folder is called.
FOLDER_TYPES = {"homeworkfolder": "exercises"}


_ARCHIVE_EXT = frozenset({"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"})


# Program files: "script.py" or "lecture_demo.m" is code, not a lecture script.
_CODE_EXT = frozenset(
    {
        "py",
        "r",
        "m",
        "jl",
        "java",
        "c",
        "h",
        "cpp",
        "hpp",
        "cs",
        "js",
        "ts",
        "sh",
        "bash",
        "ps1",
        "sql",
        "scala",
        "kt",
        "go",
        "rs",
        "rb",
        "pl",
    }
)


def file_category(name: str, own: Keywords | None = None) -> str | None:
    """Category a file name reveals ("Uebungsblatt_3.pdf" -> exercises), if any."""
    stem, _, ext = name.rpartition(".") if "." in name else (name, "", "")
    if ext.lower() == "ipynb":  # Jupyter notebooks are worked on like exercise sheets
        return "exercises"
    category = _category(stem, own)
    # "script.py", and "script.py.zip" too: the last extension, or the one before an
    # archive extension. Not any part: "Folien Kap.C.pdf" is still slides.
    parts = name.lower().split(".")[1:]
    if parts and parts[-1] in _ARCHIVE_EXT:
        parts = parts[-2:-1]
    if category == "slides" and parts and parts[-1] in _CODE_EXT:
        return None
    return category


def auto_route(
    folder_path: str,
    folder_type: str,
    name: str,
    subdirs: list[str],
    own: Keywords | None = None,
) -> tuple[str, str, str] | None:
    """(category, local folder, why) for a file no rule matches.

    The Stud.IP folder name decides first, then the Stud.IP folder type, then the
    file name - but the name only when the folder says nothing ("Material",
    "Allgemeiner Dateiordner", the course root): a folder called "Folien" keeps
    everything in it together.
    """
    candidates: list[tuple[str, str]] = []
    if (category := folder_category(folder_path, own)) is not None:
        candidates.append((category, "folder name"))
    elif (category := FOLDER_TYPES.get(folder_type.casefold())) is not None:
        candidates.append((category, "Stud.IP folder type"))
    elif (category := file_category(name, own)) is not None:
        candidates.append((category, "file name"))
    for category, why in candidates:
        target = _local_target(category, subdirs)
        if target is not None:
            return category, target, why
    return None


def local_category_dirs(subdirs: list[str], category: str) -> list[str]:
    """Local folders for ``category``, best first; archive folders are never offered."""
    keywords = LOCAL_KEYWORDS[category]
    scored: list[tuple[int, str]] = []
    for rel in subdirs:
        parts = [normalise_name(p) for p in rel.split("/")]
        old_exams = (
            category == "exams"
            and len(parts) > 1
            and _has_keyword(parts[0], keywords)
            and _has_keyword(parts[-1], (*_PAST, "altklausur"))
        )
        # "Klausur/Alt" is where old exams belong, not an archive to keep away from
        if not old_exams and any(_has_keyword(p, _ARCHIVE) for p in parts):
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


def _raw_spelling(pattern: str) -> str:
    """A pattern over normalised names ("ue", spaces) for names as they are."""
    pattern = pattern.replace("ae?", "(?:ae?|ä)")  # "blae?tt": Blatt, Blaetter, Blätter
    for plain, umlaut in (("ae", "ä"), ("oe", "ö"), ("ue", "ü")):
        pattern = re.sub(rf"{plain}(?![?|)])", f"(?:{plain}|{umlaut})", pattern)
    return pattern.replace(" ?", "[ _.+-]?")


_RAW_FALSE_FRIENDS = _raw_spelling(_FALSE_FRIENDS.pattern)


def _with_umlauts(keyword: str) -> str:
    """Regex for a normalised keyword that also matches the real spelling:
    "loesung" -> "l(?:oe|ö)sung", so the rule matches "Lösung_1.pdf" itself."""
    pattern = re.escape(keyword)
    for plain, umlaut in (("ae", "ä"), ("oe", "ö"), ("ue", "ü"), ("ss", "ß")):
        pattern = pattern.replace(plain, f"(?:{plain}|{umlaut})")
    if keyword == "ubung":  # "Übung" written without the e
        pattern = "(?:u|ü)bung"
    return pattern


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
    files: list[RemoteFile],
    course_dir: Path,
    existing_ids: set[str] | None = None,
    *,
    by_name: bool = True,
) -> list[Suggestion]:
    """One rule per top-level Stud.IP folder whose name reveals its category.

    With ``by_name``, files in the course root are classified by file name too, so
    that e.g. "Altklausur_2024.pdf" gets its own name-based rule. Pass False when
    automatic sorting is on: it already sorts by file name, with the exclusion list
    and the category order that a regex rule cannot reproduce.
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

    if not by_name:
        return suggestions
    root_files = [f for f in files if not f.folder_path]
    by_category: dict[str, int] = Counter(
        c for f in root_files if (c := file_category(f.name)) is not None
    )
    keywords = REMOTE_KEYWORDS
    # Rules apply in order: the same order as the classifier (exams before solutions
    # before exercises ...), so "Lösung Blatt 3" is a solution, not an exercise.
    priority = list(REMOTE_KEYWORDS)
    for category in sorted(by_category, key=priority.index):
        count = by_category[category]
        if any(i == f"{category}-by-name" or i.startswith(f"{category}-by-name-") for i in taken):
            continue  # added before: do not offer "-2", "-3", ... on every run
        target = _local_target(category, subdirs)
        if target is None:
            continue
        words = "|".join(_with_umlauts(k) for k in keywords[category] if len(k) > 3)
        # Never "Merkblatt", "Examples", "Laufzettel": the classifier's exclusion list
        # as a look-ahead, so the rule does not undo what automatic sorting knows.
        pattern = f"^(?!.*(?:{_RAW_FALSE_FRIENDS}))(?:.*(?:{words}))"
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


# -- a rule from one file (preview: "+ Rule") --------------------------------------------

_DIGITS = re.compile(r"\d+")


def glob_escape(text: str) -> str:
    """``text`` as a glob that matches only itself."""
    return re.sub(r"([*?\[])", r"[\1]", text)


def name_pattern(name: str) -> str | None:
    """A glob for files named like ``name``: numbers become ``*``.

    "Blatt_03.pdf" -> "Blatt_*.pdf", "VL 4 Logik.pdf" -> "VL * Logik.pdf".
    None when the name has no number (the glob would only match this file).
    """
    name = nfc(name)
    if not _DIGITS.search(name):
        return None
    # Numbers first, as a marker that cannot occur in a name: only they are merged
    # ("3.1" -> one "*"), never a "*" that was in the name itself.
    marked = re.sub(r"\x00(?:[^\w\x00]?\x00)+", "\x00", _DIGITS.sub("\x00", name))
    return glob_escape(marked).replace("\x00", "*")


def rule_from_file(
    course_rules: tuple[str, ...], file_folder: str, file_name: str, by: str, target: str
) -> dict:
    """A rule for files like this one: ``by`` is "folder" (its Stud.IP folder) or
    "name" (files named like it). ``course_rules`` are the ids already taken."""
    match: dict[str, str] = {}
    if by == "folder":
        if not file_folder:
            raise ValueError("the file is not in a Stud.IP folder")
        match["folder"] = glob_escape(nfc(file_folder))
    elif by == "name":
        pattern = name_pattern(file_name)
        if pattern is None:
            raise ValueError("the name has no number to generalise")
        match["name_glob"] = pattern
    else:
        raise ValueError(f"unknown kind {by!r}")
    return {
        "id": _unique(_slug(target), set(course_rules)),
        "match": match,
        "target": target,
    }
