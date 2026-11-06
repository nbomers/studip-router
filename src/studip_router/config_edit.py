"""Change single values in the user's config while keeping comments and layout.

Every write is validated before it replaces the file, goes through a temporary
file, and the first change of a session keeps a ``.bak`` copy.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Table

from .config import Config, parse
from .fsutil import atomic_writer, sha256_file

_backed_up: set[Path] = set()


def _load(path: Path) -> tomlkit.TOMLDocument:
    return tomlkit.parse(path.read_text(encoding="utf-8"))


def _save(path: Path, doc: tomlkit.TOMLDocument) -> Config:
    text = tomlkit.dumps(doc)
    config = parse(tomllib.loads(text), path)  # raises ConfigError, nothing written
    if path not in _backed_up:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
        _backed_up.add(path)
    original_sha = sha256_file(path)
    with atomic_writer(path, mode=0o600, may_replace=lambda p: sha256_file(p) == original_sha) as (
        fh,
        _,
    ):
        fh.write(text.encode("utf-8"))
    return config


def _table(doc: Any, keys: tuple[str, ...]) -> Any:
    node = doc
    for key in keys:
        if key not in node:
            node[key] = tomlkit.table()
        node = node[key]
    return node


def set_value(path: Path, keys: tuple[str, ...], value: Any) -> Config:
    """Set ``keys`` (e.g. ("sync", "on_update")) to ``value``; None removes the key."""
    doc = _load(path)
    table = _table(doc, keys[:-1])
    if value is None:
        if keys[-1] in table:
            del table[keys[-1]]
    else:
        table[keys[-1]] = value
    return _save(path, doc)


def get_value(path: Path, keys: tuple[str, ...], default: Any = None) -> Any:
    node: Any = _load(path)
    for key in keys:
        if not isinstance(node, (dict, Table)) or key not in node:
            return default
        node = node[key]
    return node.unwrap() if hasattr(node, "unwrap") else node


def set_course(path: Path, course_id: str, **fields: Any) -> Config:
    """Create or update ``[courses.<id>]``; a value of None removes that key."""
    doc = _load(path)
    courses = _table(doc, ("courses",))
    if course_id not in courses:
        courses[course_id] = tomlkit.table()
    course = courses[course_id]
    for key, value in fields.items():
        if value is None:
            if key in course:
                del course[key]
        else:
            course[key] = value
    return _save(path, doc)


def edit_courses(
    path: Path, *, root: str | None = None, courses: dict[str, dict[str, Any]]
) -> Config:
    """Several course changes (and optionally ``sync.root``) in one validated write.

    ``courses`` maps a course id to its fields; a value of None removes that key.
    """
    doc = _load(path)
    if root is not None:
        _table(doc, ("sync",))["root"] = root
    table = _table(doc, ("courses",))
    for course_id, fields in courses.items():
        if course_id not in table:
            table[course_id] = tomlkit.table()
        course = table[course_id]
        for key, value in fields.items():
            if value is None:
                if key in course:
                    del course[key]
            else:
                course[key] = value
    return _save(path, doc)


def remove_course(path: Path, course_id: str) -> Config:
    """Delete ``[courses.<id>]`` with its rules (a course that is gone from Stud.IP).

    The first change of a session leaves ``config.toml.bak`` next to the file.
    """
    doc = _load(path)
    courses = doc.get("courses")
    if courses is None or course_id not in courses:
        raise KeyError(course_id)
    del courses[course_id]
    return _save(path, doc)


def open_in_editor(path: Path) -> str | None:
    """Open ``path`` in $VISUAL/$EDITOR (quoted arguments allowed), else nano or vi.

    Returns a problem description instead of raising when no editor can be started.
    """
    configured = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    try:
        command = shlex.split(configured) if configured else []
    except ValueError as exc:
        return f"cannot read $VISUAL/$EDITOR ({exc})"
    if not command:
        found = shutil.which("nano") or shutil.which("vi")
        if found is None:
            return "no editor found; set $EDITOR, e.g. export EDITOR=nano"
        command = [found]
    try:
        subprocess.run([*command, str(path)], check=False)
    except OSError as exc:
        return f"could not start {command[0]!r}: {exc.strerror or exc}"
    return None
