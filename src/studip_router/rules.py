"""Routing rules: ordered per course, first match wins."""

from __future__ import annotations

import fnmatch
import re
import string
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any

from .fsutil import nfc, split_ext
from .models import RemoteFile

MATCH_KEYS = {
    "folder",
    "name_glob",
    "name_regex",
    "ext",
    "mime",
    "min_size_mb",
    "max_size_mb",
    "changed_after",
    "changed_before",
    "description_regex",
    "case_sensitive",
    "folder_type",
}
RULE_KEYS = {"id", "match", "target", "rename", "unzip", "skip"}
TEMPLATE_VARS = {
    "course",
    "semester",
    "orig_name",
    "orig_stem",
    "ext",
    "folder",
    "folder_path",
    "date",
    "0",
}


@dataclass(frozen=True)
class Match:
    folder: str | None = None
    name_glob: str | None = None
    name_regex: re.Pattern[str] | None = None
    ext: tuple[str, ...] = ()
    mime: tuple[str, ...] = ()
    min_size_mb: float | None = None
    max_size_mb: float | None = None
    changed_after: date | None = None
    changed_before: date | None = None
    description_regex: re.Pattern[str] | None = None
    case_sensitive: bool = False
    folder_type: tuple[str, ...] = ()  # e.g. ("HomeworkFolder",), case-insensitive

    def test(self, file: RemoteFile) -> tuple[bool, re.Match[str] | None]:
        """Return (matched, regex match of name_regex for template groups)."""
        fold = (lambda s: s) if self.case_sensitive else str.casefold
        name = nfc(file.name)
        if self.folder is not None and not fnmatch.fnmatchcase(
            fold(nfc(file.folder_path)), fold(nfc(self.folder))
        ):
            return False, None
        if self.name_glob is not None and not fnmatch.fnmatchcase(
            fold(name), fold(nfc(self.name_glob))
        ):
            return False, None
        if self.folder_type and file.folder_type.casefold() not in self.folder_type:
            return False, None
        if self.ext:
            ext = split_ext(name)[1].lstrip(".").lower()
            if ext not in self.ext:
                return False, None
        if self.mime and not any(fnmatch.fnmatchcase(file.mime_type.lower(), m) for m in self.mime):
            return False, None
        mb = file.size / (1024 * 1024)
        if self.min_size_mb is not None and mb < self.min_size_mb:
            return False, None
        if self.max_size_mb is not None and mb > self.max_size_mb:
            return False, None
        changed = file.chdate.date()
        if self.changed_after is not None and changed < self.changed_after:
            return False, None
        if self.changed_before is not None and changed > self.changed_before:
            return False, None
        if self.description_regex is not None and not self.description_regex.search(
            file.description
        ):
            return False, None
        regex_match = None
        if self.name_regex is not None:
            regex_match = self.name_regex.search(name)
            if regex_match is None:
                return False, None
        return True, regex_match


@dataclass(frozen=True)
class Rule:
    id: str
    match: Match
    target: str | None  # None when skip
    rename: str | None = None
    unzip: bool | None = None
    skip: bool = False


def _compile(pattern: Any, where: str, problems: list[str], case_sensitive: bool):
    if not isinstance(pattern, str):
        problems.append(f"{where}: expected a string")
        return None
    try:
        return re.compile(nfc(pattern), 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        problems.append(f"{where}: invalid regex: {exc}")
        return None


def _date(value: Any, where: str, problems: list[str]) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    problems.append(f"{where}: expected a date like 2026-10-01")
    return None


def _str_tuple(value: Any, where: str, problems: list[str]) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{where}: expected a string or list of strings")
        return ()
    return tuple(value)


def _number(value: Any, where: str, problems: list[str]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        problems.append(f"{where}: expected a non-negative number")
        return None
    return float(value)


def check_template(template: str, regex: re.Pattern[str] | None) -> str | None:
    """Return an error message if the template references unknown variables."""
    allowed = set(TEMPLATE_VARS)
    if regex is not None:
        allowed |= {str(i) for i in range(1, regex.groups + 1)}
        allowed |= set(regex.groupindex)
    try:
        fields = [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]
    except ValueError as exc:
        return f"invalid template: {exc}"
    for field in fields:
        if "." in field or "[" in field:
            return f"{{{field}}}: attribute and index access is not allowed in templates"
        key = field
        if key not in allowed:
            hint = " (add a capture group to name_regex)" if key.isdigit() else ""
            return f"unknown template variable {{{key}}}{hint}"
    return None


def _target(value: Any, where: str, problems: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: expected a non-empty folder path")
        return None
    p = PurePosixPath(nfc(value.strip()))
    if p.is_absolute() or ".." in p.parts:
        problems.append(f"{where}: must be a relative path inside the course folder")
        return None
    return str(p)


def parse_match(raw: Any, where: str, problems: list[str]) -> Match:
    if not isinstance(raw, dict):
        problems.append(f"{where}: expected a table")
        return Match()
    for key in raw.keys() - MATCH_KEYS:
        problems.append(f"{where}.{key}: unknown key (allowed: {', '.join(sorted(MATCH_KEYS))})")
    cs = raw.get("case_sensitive", False)
    if not isinstance(cs, bool):
        problems.append(f"{where}.case_sensitive: expected bool")
        cs = False
    values: dict[str, Any] = {"case_sensitive": cs}
    for key in ("folder", "name_glob"):
        if key in raw:
            if isinstance(raw[key], str):
                values[key] = raw[key]
            else:
                problems.append(f"{where}.{key}: expected a string")
    for key in ("name_regex", "description_regex"):
        if key in raw:
            values[key] = _compile(raw[key], f"{where}.{key}", problems, cs)
    if "ext" in raw:
        values["ext"] = tuple(
            e.lower().lstrip(".") for e in _str_tuple(raw["ext"], f"{where}.ext", problems)
        )
    if "folder_type" in raw:
        values["folder_type"] = tuple(
            t.casefold() for t in _str_tuple(raw["folder_type"], f"{where}.folder_type", problems)
        )
    if "mime" in raw:
        values["mime"] = tuple(
            m.lower() for m in _str_tuple(raw["mime"], f"{where}.mime", problems)
        )
    for key in ("min_size_mb", "max_size_mb"):
        if key in raw:
            values[key] = _number(raw[key], f"{where}.{key}", problems)
    for key in ("changed_after", "changed_before"):
        if key in raw:
            values[key] = _date(raw[key], f"{where}.{key}", problems)
    return Match(**values)


def parse_rules(raw_rules: Any, where: str) -> tuple[tuple[Rule, ...], list[str]]:
    problems: list[str] = []
    if not isinstance(raw_rules, list):
        return (), [f"{where}: expected an array of tables ([[...rules]])"]
    rules: list[Rule] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_rules):
        here = f"{where}[{i}]"
        if not isinstance(raw, dict):
            problems.append(f"{here}: expected a table")
            continue
        for key in raw.keys() - RULE_KEYS:
            problems.append(f"{here}.{key}: unknown key (allowed: {', '.join(sorted(RULE_KEYS))})")
        rule_id = raw.get("id", f"rule-{i + 1}")
        if not isinstance(rule_id, str) or not rule_id:
            problems.append(f"{here}.id: expected a non-empty string")
            rule_id = f"rule-{i + 1}"
        if rule_id in seen:
            problems.append(f"{here}.id: duplicate id {rule_id!r}")
        seen.add(rule_id)
        if "match" not in raw:
            problems.append(f"{here}.match: required (use match = {{}} to match everything)")
        match = parse_match(raw.get("match", {}), f"{here}.match", problems)
        skip = raw.get("skip", False)
        if not isinstance(skip, bool):
            problems.append(f"{here}.skip: expected bool")
            skip = False
        target = None
        if not skip:
            if "target" not in raw:
                problems.append(f"{here}.target: required (or set skip = true)")
            else:
                target = _target(raw["target"], f"{here}.target", problems)
        rename = raw.get("rename")
        if rename is not None:
            if not isinstance(rename, str) or not rename.strip():
                problems.append(f"{here}.rename: expected a non-empty template string")
                rename = None
            elif error := check_template(rename, match.name_regex):
                problems.append(f"{here}.rename: {error}")
        unzip = raw.get("unzip")
        if unzip is not None and not isinstance(unzip, bool):
            problems.append(f"{here}.unzip: expected bool")
            unzip = None
        rules.append(Rule(rule_id, match, target, rename, unzip, skip))
    return tuple(rules), problems


def first_match(rules: tuple[Rule, ...], file: RemoteFile) -> tuple[Rule | None, re.Match | None]:
    for rule in rules:
        matched, regex_match = rule.match.test(file)
        if matched:
            return rule, regex_match
    return None, None
