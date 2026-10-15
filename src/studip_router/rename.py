"""Rename engine: templates with variables/regex groups, then normalisation."""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, replace
from typing import Any

from .fsutil import nfc, sanitize_filename, split_ext
from .models import RemoteFile

SPACES = ("keep", "_", "-")
UMLAUTS = ("keep", "transliterate")
TRANSLIT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)


class RenameError(Exception):
    pass


@dataclass(frozen=True)
class RenameOptions:
    pad_numbers: int = 2  # 0 disables; "lecture-1" -> "lecture-01"
    spaces: str = "keep"
    umlauts: str = "keep"
    lowercase: bool = False
    strip_prefixes: tuple[str, ...] = ()
    portable: bool = True  # replace characters Windows/OneDrive/iCloud reject

    def merged(self, overrides: dict[str, Any]) -> RenameOptions:
        values = dict(overrides)
        if "strip_prefixes" in values:
            values["strip_prefixes"] = tuple(values["strip_prefixes"])
        return replace(self, **values)


class _Formatter(string.Formatter):
    """str.format with two twists: numeric strings accept int format specs
    ({1:02} on "7" gives "07"), and missing groups render as ""."""

    def format_field(self, value: Any, format_spec: str) -> str:
        if isinstance(value, str) and value.isdigit() and format_spec:
            value = int(value)
        return super().format_field(value, format_spec)

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> Any:
        if "." in field_name or "[" in field_name:  # no {orig_name.__class__} tricks
            raise RenameError(f"attribute and index access is not allowed: {{{field_name}}}")
        return super().get_field(field_name, args, kwargs)

    def get_value(self, key: int | str, args: Any, kwargs: Any) -> Any:
        if isinstance(key, int):
            key = str(key)
        if key not in kwargs:
            raise RenameError(f"unknown template variable {{{key}}}")
        value = kwargs[key]
        return "" if value is None else value


_FORMATTER = _Formatter()


def template_variables(
    file: RemoteFile, course: str, semester: str, match: re.Match[str] | None
) -> dict[str, Any]:
    stem, ext = split_ext(nfc(file.name))
    folder_path = nfc(file.folder_path)
    variables: dict[str, Any] = {
        "course": course,
        "semester": semester,
        "orig_name": nfc(file.name),
        "orig_stem": stem,
        "ext": ext,
        "folder": folder_path.rsplit("/", 1)[-1] if folder_path else "",
        "folder_path": folder_path,
        "date": file.chdate.strftime("%Y-%m-%d"),
    }
    if match is not None:
        variables["0"] = match.group(0)
        for i, group in enumerate(match.groups(), start=1):
            variables[str(i)] = group
        variables.update({k: v for k, v in match.groupdict().items()})
    return variables


def render(template: str, variables: dict[str, Any]) -> str:
    try:
        return _FORMATTER.vformat(template, (), variables)
    except (ValueError, IndexError) as exc:
        raise RenameError(f"invalid template {template!r}: {exc}") from exc


# Only numbers standing on their own ("Blatt 3", "lecture-1", "01_intro", "Teil 1.2"):
# never digits that belong to a word like "H2O", "Python3", "IPv6", "A4" or "2x2".
_SHORT_NUMBER = r"(?<![^\s_\-.(\[#])(\d{{1,{n}}})(?![^\s_\-.)\]])"


def normalise(name: str, options: RenameOptions, mime_type: str = "") -> str:
    stem, ext = split_ext(nfc(name))
    for prefix in options.strip_prefixes:
        if stem.casefold().startswith(prefix.casefold()) and len(stem) > len(prefix):
            stem = stem[len(prefix) :]
            break
    if options.umlauts == "transliterate":
        stem = stem.translate(TRANSLIT)
    if options.spaces != "keep":
        stem = re.sub(r"\s+", options.spaces, stem.strip())
    if options.lowercase:
        stem, ext = stem.lower(), ext.lower()
    if options.pad_numbers > 1:
        width = options.pad_numbers
        stem = re.sub(_SHORT_NUMBER.format(n=width - 1), lambda m: m.group(1).zfill(width), stem)
    return sanitize_filename(stem + ext, portable=options.portable, mime_type=mime_type)


def target_name(
    file: RemoteFile,
    template: str | None,
    options: RenameOptions,
    *,
    course: str = "",
    semester: str = "",
    match: re.Match[str] | None = None,
) -> str:
    name = nfc(file.name)
    if template:
        name = render(template, template_variables(file, course, semester, match))
        if "/" in name:
            raise RenameError(f"template {template!r} produced a path: {name!r}")
        if not name.strip():
            raise RenameError(f"template {template!r} produced an empty name")
    return normalise(name, options, file.mime_type)
