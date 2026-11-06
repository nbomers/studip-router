"""Shared CLI apps, option types, output and configuration helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from . import auth, paths
from .api import StudipClient
from .config import Config, ConfigError, CourseConfig, load
from .report import esc

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Sync Stud.IP course files into your own folders, with routing and renaming rules.",
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)

auth_app = typer.Typer(no_args_is_help=True, help="Manage the stored password.")

config_app = typer.Typer(no_args_is_help=True, help="Inspect and edit the configuration.")

rules_app = typer.Typer(no_args_is_help=True, help="Inspect, test and suggest routing rules.")

schedule_app = typer.Typer(no_args_is_help=True, help="Opt-in background sync.")

app.add_typer(auth_app, name="auth")

app.add_typer(config_app, name="config")

app.add_typer(rules_app, name="rules")

app.add_typer(schedule_app, name="schedule")

ignore_app = typer.Typer(
    no_args_is_help=True,
    help="Files you never want (otherwise deleted files come back with the next sync).",
)

app.add_typer(ignore_app, name="ignore")

pdf_app = typer.Typer(
    no_args_is_help=True,
    help="Unlock a course's password-protected PDFs (one shared password per course).",
)

app.add_typer(pdf_app, name="pdf-password")

console = Console(highlight=False)

err = Console(stderr=True, highlight=False)


class _Opts:
    json = False
    quiet = False


OPTS = _Opts()

ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Use this config file.")]

CourseOpt = Annotated[
    str | None, typer.Option("--course", help="Course ID, title or target (substring).")
]


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (Path, Enum)):
        return str(obj.value if isinstance(obj, Enum) else obj)
    return obj


def emit_json(obj: Any) -> None:
    print(json.dumps(_jsonable(obj), ensure_ascii=False, indent=2, default=str))


def fail(message: str, code: int = 1) -> typer.Exit:
    if OPTS.json:
        emit_json({"error": message})
    else:
        err.print(f"[red]Error:[/red] {esc(message)}")
    return typer.Exit(code)


def load_config(path: Path | None = None) -> Config:
    try:
        return load(path or paths.config_file())
    except ConfigError as exc:
        raise fail(f"invalid config {exc}", 2) from None


def open_client(cfg: Config) -> StudipClient:
    try:
        password = auth.get_password(cfg.base_url, cfg.username)
    except auth.AuthError as exc:
        raise fail(str(exc), 3) from None
    return StudipClient(cfg.base_url, cfg.username, password)


def find_course(cfg: Config, query: str) -> CourseConfig:
    q = query.casefold()
    if query in cfg.courses:
        return cfg.courses[query]
    hits = [
        c
        for c in cfg.courses.values()
        if c.id.startswith(query) or q in c.name.casefold() or q in c.target.casefold()
    ]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise fail(f"no configured course matches {query!r}")
    raise fail(f"{query!r} is ambiguous: " + ", ".join(c.name or c.id for c in hits))
