"""Load and validate the TOML configuration.

Errors carry the key path (``courses.<id>.target``) so users can find them;
TOML syntax errors additionally carry line and column from ``tomllib``.
"""

from __future__ import annotations

import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

ON_UPDATE = ("version", "overwrite", "skip")
ON_CONFLICT = ("suffix", "skip", "error")


class ConfigError(Exception):
    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(f"{path}:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class ExcludeConfig:
    globs: tuple[str, ...] = ()
    mime: tuple[str, ...] = ()
    max_size_mb: float | None = None


@dataclass(frozen=True)
class CourseConfig:
    id: str
    name: str = ""
    target: str = ""
    exclude: bool = False
    default_target: str | None = None
    exclude_files: ExcludeConfig = ExcludeConfig()
    rules: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class Config:
    path: Path
    base_url: str
    username: str
    root: Path
    semester: str = "current"
    only_configured_courses: bool = True
    on_update: str = "version"
    on_conflict: str = "suffix"
    default_target: str = "Sonstiges"
    create_missing_dirs: bool = False
    exclude: ExcludeConfig = ExcludeConfig(mime=("video/*",), max_size_mb=200)
    rename: dict[str, Any] = field(default_factory=dict)
    courses: dict[str, CourseConfig] = field(default_factory=dict)

    def course_dir(self, course: CourseConfig) -> Path:
        target = Path(course.target).expanduser()
        return target if target.is_absolute() else self.root / target

    def course_default_target(self, course: CourseConfig) -> str:
        return course.default_target or self.default_target

    def course_exclude(self, course: CourseConfig) -> ExcludeConfig:
        """Global exclusions extended (and size limit overridden) by the course's own."""
        own = course.exclude_files
        return ExcludeConfig(
            globs=self.exclude.globs + own.globs,
            mime=self.exclude.mime + own.mime,
            max_size_mb=own.max_size_mb
            if own.max_size_mb is not None
            else self.exclude.max_size_mb,
        )


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


class _Validator:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def table(self, data: Any, where: str, allowed: set[str]) -> dict[str, Any]:
        if not isinstance(data, dict):
            self.problems.append(f"{where}: expected a table")
            return {}
        for key in data.keys() - allowed:
            hint = f" (allowed: {', '.join(sorted(allowed))})" if allowed else ""
            self.problems.append(f"{where}.{key}: unknown key{hint}")
        return data

    def get(
        self,
        data: dict,
        key: str,
        where: str,
        kind: type | tuple,
        default: Any = None,
        required: bool = False,
    ) -> Any:
        if key not in data:
            if required:
                self.problems.append(f"{where}.{key}: required")
            return default
        value = data[key]
        # bool is an int subclass; don't accept true for a number
        if not isinstance(value, kind) or (isinstance(value, bool) and bool not in _kinds(kind)):
            names = "/".join(k.__name__ for k in _kinds(kind))
            self.problems.append(f"{where}.{key}: expected {names}, got {type(value).__name__}")
            return default
        return value

    def choice(
        self, data: dict, key: str, where: str, options: tuple[str, ...], default: str
    ) -> str:
        value = self.get(data, key, where, str, default)
        if value not in options:
            self.problems.append(f"{where}.{key}: must be one of {', '.join(options)}")
            return default
        return value

    def str_list(self, data: dict, key: str, where: str) -> tuple[str, ...] | None:
        value = self.get(data, key, where, list)
        if value is None:
            return None
        if not all(isinstance(v, str) for v in value):
            self.problems.append(f"{where}.{key}: expected a list of strings")
            return None
        return tuple(value)

    def rel_path(self, value: str, where: str, allow_absolute: bool) -> str:
        value = nfc(value.strip())
        if not value:
            self.problems.append(f"{where}: must not be empty")
            return value
        p = PurePosixPath(value)
        if (p.is_absolute() or value.startswith("~")) and not allow_absolute:
            self.problems.append(f"{where}: must be relative to the course folder")
        if ".." in p.parts:
            self.problems.append(f"{where}: '..' is not allowed")
        return value

    def exclude(self, data: Any, where: str, base: ExcludeConfig) -> ExcludeConfig:
        data = self.table(data, where, {"globs", "mime", "max_size_mb"})
        size = self.get(data, "max_size_mb", where, (int, float), base.max_size_mb)
        if size is not None and size <= 0:
            self.problems.append(f"{where}.max_size_mb: must be positive")
        globs = self.str_list(data, "globs", where)
        mime = self.str_list(data, "mime", where)
        return ExcludeConfig(
            globs=base.globs if globs is None else globs,
            mime=base.mime if mime is None else mime,
            max_size_mb=size,
        )


def _kinds(kind: type | tuple) -> tuple[type, ...]:
    return kind if isinstance(kind, tuple) else (kind,)


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            path, ["file not found; run `studip init` or copy config.example.toml"]
        ) from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(path, [f"TOML syntax error: {exc}"]) from exc
    return parse(raw, path)


def parse(raw: dict[str, Any], path: Path) -> Config:
    v = _Validator()
    top = v.table(raw, "config", {"server", "sync", "rename", "courses"})

    server = v.table(top.get("server", {}), "server", {"base_url", "username"})
    base_url = v.get(server, "base_url", "server", str, "", required=True)
    if base_url and urlsplit(base_url).scheme not in ("http", "https"):
        v.problems.append("server.base_url: must start with https:// (or http://)")
    username = v.get(server, "username", "server", str, "", required=True)

    sync = v.table(
        top.get("sync", {}),
        "sync",
        {
            "root",
            "semester",
            "only_configured_courses",
            "on_update",
            "on_conflict",
            "default_target",
            "create_missing_dirs",
            "exclude",
        },
    )
    root = v.get(sync, "root", "sync", str, "", required=True)
    default_target = v.rel_path(
        v.get(sync, "default_target", "sync", str, "Sonstiges"), "sync.default_target", False
    )
    defaults = Config.__dataclass_fields__["exclude"].default
    global_exclude = v.exclude(sync.get("exclude", {}), "sync.exclude", defaults)

    rename = v.table(top.get("rename", {}), "rename", set(top.get("rename", {}) or {}))

    courses: dict[str, CourseConfig] = {}
    for cid, course in v.table(
        top.get("courses", {}), "courses", set(top.get("courses") or {})
    ).items():
        where = f"courses.{cid}"
        course = v.table(
            course,
            where,
            {"name", "target", "exclude", "default_target", "exclude_files", "rules"},
        )
        excluded = v.get(course, "exclude", where, bool, False)
        target = v.get(course, "target", where, str, "", required=not excluded)
        if target:
            target = v.rel_path(target, f"{where}.target", allow_absolute=True)
        course_default = v.get(course, "default_target", where, str)
        if course_default is not None:
            course_default = v.rel_path(course_default, f"{where}.default_target", False)
        rules = v.get(course, "rules", where, list, [])
        courses[cid] = CourseConfig(
            id=cid,
            name=v.get(course, "name", where, str, ""),
            target=target,
            exclude=excluded,
            default_target=course_default,
            exclude_files=v.exclude(
                course.get("exclude_files", {}), f"{where}.exclude_files", ExcludeConfig()
            ),
            rules=tuple(rules),
        )

    config = Config(
        path=path,
        base_url=base_url.rstrip("/"),
        username=username,
        root=Path(nfc(root)).expanduser(),
        semester=v.get(sync, "semester", "sync", str, "current"),
        only_configured_courses=v.get(sync, "only_configured_courses", "sync", bool, True),
        on_update=v.choice(sync, "on_update", "sync", ON_UPDATE, "version"),
        on_conflict=v.choice(sync, "on_conflict", "sync", ON_CONFLICT, "suffix"),
        default_target=default_target,
        create_missing_dirs=v.get(sync, "create_missing_dirs", "sync", bool, False),
        exclude=global_exclude,
        rename=dict(rename),
        courses=courses,
    )
    if v.problems:
        raise ConfigError(path, v.problems)
    return config
