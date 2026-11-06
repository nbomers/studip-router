"""CLI commands grouped by user workflow."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import typer
from rich.prompt import Confirm
from rich.table import Table
from rich.text import Text

from .api import ApiError, AuthFailed
from .cli_context import OPTS as OPTS
from .cli_context import ConfigOpt as ConfigOpt
from .cli_context import CourseOpt as CourseOpt
from .cli_context import _jsonable as _jsonable
from .cli_context import app as app
from .cli_context import auth_app as auth_app
from .cli_context import config_app as config_app
from .cli_context import console as console
from .cli_context import emit_json as emit_json
from .cli_context import err as err
from .cli_context import fail as fail
from .cli_context import find_course as find_course
from .cli_context import ignore_app as ignore_app
from .cli_context import load_config as load_config
from .cli_context import open_client as open_client
from .cli_context import pdf_app as pdf_app
from .cli_context import rules_app as rules_app
from .cli_context import schedule_app as schedule_app
from .models import RemoteFile
from .planner import build_router
from .rename import RenameError
from .report import esc
from .rules import match_values
from .suggest import suggest_rules
from .tomlwrite import append_blocks, rule_block


@rules_app.command("list")
def rules_list(
    course: Annotated[str | None, typer.Argument()] = None, config: ConfigOpt = None
) -> None:
    """Show the rules of all (or one) course in evaluation order."""
    cfg = load_config(config)
    courses = (
        [find_course(cfg, course)] if course else [c for c in cfg.courses.values() if not c.exclude]
    )
    if OPTS.json:
        emit_json(
            {
                c.id: [
                    {
                        "id": r.id,
                        "match": match_values(r.match),
                        "target": r.target,
                        "rename": r.rename,
                        "skip": r.skip,
                    }
                    for r in c.rules
                ]
                for c in courses
            }
        )
        return
    for c in courses:
        table = Table(title=esc(f"{c.name or c.id} → {c.target}"), title_justify="left")
        for col in ("#", "id", "match", "target", "rename"):
            table.add_column(col)
        for i, r in enumerate(c.rules, 1):
            m = r.match
            parts = [f"{key}={value}" for key, value in match_values(m).items()]
            table.add_row(
                str(i),
                Text(r.id),
                Text(" ".join(parts) or "*"),
                "[dim]skip[/dim]" if r.skip else Text(str(r.target)),
                Text(r.rename or ""),
            )
        table.add_row("", "[dim]default[/dim]", "*", Text(cfg.course_default_target(c)), "")
        console.print(table)


@rules_app.command("test")
def rules_test(
    course: str,
    filename: str,
    folder: Annotated[str, typer.Option("--folder", "-f", help="Stud.IP folder path.")] = "",
    size_mb: Annotated[float, typer.Option("--size", help="File size in MB.")] = 0.1,
    mime: Annotated[
        str, typer.Option("--mime", help="MIME type to test with (adds a missing extension).")
    ] = "application/octet-stream",
    config: ConfigOpt = None,
) -> None:
    """Show which rule a file would match and where it would end up (no download)."""
    cfg = load_config(config)
    c = find_course(cfg, course)
    file = RemoteFile(
        id="test",
        course_id=c.id,
        name=filename,
        folder_path=folder,
        size=int(size_mb * 1024 * 1024),
        chdate=datetime.now().astimezone(),
        mime_type=mime,
        description="",
        downloadable=True,
    )
    try:
        route = build_router(cfg, c)(file)
    except RenameError as exc:
        raise fail(str(exc)) from None
    dest = cfg.course_dir(c) / route.subdir / route.name
    data = {
        "rule": route.rule_id or "default",
        "skip": route.skip,
        "target": None if route.skip else str(dest),
        "unzip": route.unzip,
    }
    if OPTS.json:
        emit_json(data)
    elif route.skip:
        console.print(f"Rule [bold]{esc(route.rule_id)}[/bold]: [dim]skipped[/dim]")
    else:
        console.print(
            f"Rule [bold]{esc(route.rule_id or 'default target')}[/bold]\n  → {esc(dest)}"
        )


@rules_app.command("suggest")
def rules_suggest(
    course: CourseOpt = None,
    write: Annotated[
        bool, typer.Option("--write", help="Append to the config (asks first).")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before writing.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Suggest rules from Stud.IP folder names and your existing subfolders."""
    cfg = load_config(config)
    targets = (
        [find_course(cfg, course)] if course else [c for c in cfg.courses.values() if not c.exclude]
    )
    blocks: list[str] = []
    found: dict[str, list] = {}
    with open_client(cfg) as client:
        for c in targets:
            try:
                files = client.files(c.id)
            except AuthFailed as exc:  # do not repeat a rejected login for every course
                raise fail(str(exc), 3) from None
            except ApiError as exc:
                why = "files closed to you, skipped" if exc.no_access else str(exc)
                err.print(f"[yellow]{esc(c.name)}: {esc(why)}[/yellow]")
                continue
            existing = {r.id for r in c.rules}
            known_folders = {r.match.folder for r in c.rules if r.match.folder}
            suggestions = [
                s
                for s in suggest_rules(
                    files, cfg.course_dir(c), existing, by_name=not cfg.auto_route, own=cfg.keywords
                )
                if s.rule["match"].get("folder") not in known_folders
            ]
            found[c.id] = suggestions
            blocks += [
                rule_block(c.id, s.rule, f"{c.name}: {s.reason} ({s.files} files)")
                for s in suggestions
            ]
    if OPTS.json:
        emit_json(
            {
                cid: [{"rule": s.rule, "reason": s.reason, "files": s.files} for s in ss]
                for cid, ss in found.items()
            }
        )
    else:
        if not blocks:
            console.print("No new suggestions.")
            return
        # plain print: rich would read "[[courses...]]" as markup
        print("\n".join(blocks))
    if write and blocks:
        if not yes and not Confirm.ask(f"Append {len(blocks)} rule(s) to {esc(cfg.path)}?"):
            raise typer.Exit()
        backup = append_blocks(cfg.path, blocks, "rules suggested by `studip rules suggest`")
        console.print(f"[green]✓[/green] Appended. Backup: {esc(backup)}")
        load_config(cfg.path)
