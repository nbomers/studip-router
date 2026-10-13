"""Append-only TOML writing for the few things the tool adds to a user's config.

We never rewrite the user's file: new blocks are appended at the end (valid
TOML for ``[[courses.<id>.rules]]``) after a backup copy is made, so comments
and formatting survive.
"""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def key(name: str) -> str:
    return name if _BARE_KEY.match(name) else json.dumps(name, ensure_ascii=False)


def value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        # Literal strings keep regexes readable; fall back to basic strings if needed.
        if "'" not in v and "\n" not in v:
            return f"'{v}'"
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{key(k)} = {value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def rule_block(course_id: str, rule: dict[str, Any], comment: str = "") -> str:
    lines = []
    if comment:
        lines.append(f"# {comment}")
    lines.append(f"[[courses.{key(course_id)}.rules]]")
    lines += [f"{key(k)} = {value(v)}" for k, v in rule.items()]
    return "\n".join(lines) + "\n"


def append_blocks(path: Path, blocks: list[str], header: str) -> Path:
    """Append blocks to ``path`` after writing ``path.bak``. Returns the backup path.

    The result is parsed before it replaces the original, so a bug here can
    never leave the user with a broken config.
    """
    original = path.read_text(encoding="utf-8")
    addition = f"\n# --- {header} ---\n" + "\n".join(blocks)
    combined = original.rstrip("\n") + "\n" + addition
    tomllib.loads(combined)  # raises before anything is written
    backup = path.with_name(path.name + ".bak")
    shutil.copy2(path, backup)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(combined, encoding="utf-8")
    tmp.replace(path)
    return backup
