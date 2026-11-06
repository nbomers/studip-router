"""CLI commands grouped by user workflow."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.prompt import Confirm

from . import doctor, paths, schedule
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
from .dates import check_dates, upcoming_exams
from .notify import notify
from .report import esc
from .state import State


@schedule_app.command("install")
def schedule_install(
    every: Annotated[int, typer.Option("--every", help="Interval in minutes.")] = 120,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before installing.")] = False,
) -> None:
    """Run `studip sync --notify` periodically (launchd on macOS, systemd on Linux)."""
    if every < 15:
        raise fail("please use an interval of at least 15 minutes")
    exe = schedule.executable()
    if warning := schedule.executable_warning(exe):
        err.print(f"[yellow]Warning:[/yellow] {esc(warning)}")
    if not yes and not Confirm.ask(
        f"Install a background job running `{exe} sync` every {every} minutes?"
    ):
        raise typer.Exit()
    try:
        where = schedule.install(every, paths.profile())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise fail(f"could not install: {exc}") from None
    console.print(
        f"[green]✓[/green] Installed ({esc(where)}). Remove with `studip schedule uninstall`."
    )


@schedule_app.command("run-now")
def schedule_run_now() -> None:
    """Start the background job once now (to check keychain access and notifications)."""
    try:
        ok = schedule.run_now(paths.profile())
    except OSError as exc:
        raise fail(str(exc)) from None
    if not ok:
        raise fail("no background job installed; `studip schedule install` first")
    console.print(
        "Started. Watch for a notification; the log is "
        f"{paths.state_dir() / 'scheduler.log'}.\n"
        "If macOS asks for keychain access, choose [b]Always Allow[/b] - "
        "otherwise the background sync cannot read your password."
    )


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    """Remove the background job."""
    removed = schedule.uninstall(paths.profile())
    console.print("Removed." if removed else "No background job installed.")


@schedule_app.command("status")
def schedule_status() -> None:
    """Show whether the background job is installed."""
    st = schedule.status(paths.profile())
    if OPTS.json:
        emit_json(st)
        return
    if not st.installed:
        console.print("Not installed.")
        return
    every = f"every {st.interval_minutes} min, " if st.interval_minutes else ""
    console.print(
        f"Installed ({every}{esc('loaded' if st.loaded else 'not loaded')}): {esc(st.path)}"
    )


@app.command("doctor")
def doctor_cmd(
    offline: Annotated[bool, typer.Option("--offline", help="Do not contact Stud.IP.")] = False,
    test_notification: Annotated[
        bool, typer.Option("--test-notification", help="Send a test desktop notification.")
    ] = False,
) -> None:
    """Check config, folders, keychain, server and scheduler."""
    if test_notification:
        shown = notify("studip-router", "Test notification - notifications work.")
        console.print(
            "Sent. If nothing appeared, allow notifications for 'Script Editor' "
            "in System Settings > Notifications."
            if shown
            else "[yellow]No notification mechanism available.[/yellow]"
        )
    checks = list(doctor.run_checks(offline=offline))
    if OPTS.json:
        emit_json(checks)
    else:
        marks = {
            "ok": "[green]✓[/green]",
            "info": "[blue]i[/blue]",
            "warn": "[yellow]![/yellow]",
            "fail": "[red]✗[/red]",
        }
        for c in checks:
            console.print(f"{marks[c.level]} [bold]{esc(c.name)}[/bold]: {esc(c.detail)}")
    if any(c.level == "fail" for c in checks):
        raise typer.Exit(1)


@ignore_app.command("list")
def ignore_list() -> None:
    """Show ignored files."""
    with State(paths.state_db()) as state:
        rows = state.ignored()
    if OPTS.json:
        emit_json([dict(r) for r in rows])
        return
    if not rows:
        console.print("No ignored files.")
    for r in rows:
        console.print(f"{esc(r['file_id'])}  {esc(r['name'])}  [dim]since {r['since'][:10]}[/dim]")


@ignore_app.command("add")
def ignore_add(
    query: Annotated[str, typer.Argument(help="Stud.IP file id, or part of the file name.")],
    config: ConfigOpt = None,
) -> None:
    """Never download this file again (it is not deleted locally)."""
    cfg = load_config(config)
    q = query.casefold()
    with State(paths.state_db()) as state:
        hits = [
            (r.file_id, r.course_id, r.remote_name)
            for r in state.all()
            if r.file_id == query or q in r.remote_name.casefold()
        ]
        if not hits:
            try:
                with open_client(cfg) as client:
                    for c in cfg.courses.values():
                        if c.exclude:
                            continue
                        hits += [
                            (f.id, c.id, f.name)
                            for f in client.files(c.id)
                            if f.id == query or q in f.name.casefold()
                        ]
            except ApiError as exc:  # stops at the first error, also a rejected login
                raise fail(str(exc), 3 if isinstance(exc, AuthFailed) else 1) from None
        if len(hits) != 1:
            names = "\n  ".join(f"{fid}  {name}" for fid, _, name in hits[:20])
            raise fail(
                f"{len(hits)} files match {query!r}" + (f"; use the id:\n  {names}" if hits else "")
            )
        fid, cid, name = hits[0]
        state.ignore(fid, cid, name)
    console.print(f"[green]✓[/green] {esc(name)} will not be downloaded any more.")


@ignore_app.command("remove")
def ignore_remove(query: Annotated[str, typer.Argument(help="File id or name.")]) -> None:
    """Download this file again from the next sync on."""
    with State(paths.state_db()) as state:
        hits = [
            r
            for r in state.ignored()
            if r["file_id"] == query or query.casefold() in r["name"].casefold()
        ]
        if len(hits) != 1:
            raise fail(f"{len(hits)} ignored files match {query!r}")
        state.unignore(hits[0]["file_id"])
    console.print(f"[green]✓[/green] {esc(hits[0]['name'])} is synced again.")


@app.command("dates")
def dates_cmd(
    config: ConfigOpt = None,
    offline: Annotated[bool, typer.Option("--offline", help="Do not contact Stud.IP.")] = False,
    days: Annotated[int, typer.Option("--days", help="Changes found in the last N days.")] = 14,
    ics: Annotated[
        Path | None,
        typer.Option("--ics", help="Write all known course dates to this calendar file."),
    ] = None,
) -> None:
    """Cancelled, moved or re-roomed course dates, and upcoming exams."""
    cfg = load_config(config)
    now = datetime.now().astimezone()
    with State(paths.state_db()) as state:
        first = not state.all_course_dates()
        if not offline:
            courses = [(c.id, c.name or c.target) for c in cfg.courses.values() if not c.exclude]
            try:
                with open_client(cfg) as client:
                    check_dates(client, state, courses, now)
            except ApiError as exc:
                code = 3 if isinstance(exc, AuthFailed) else 1
                raise fail(f"{exc} (use --offline for the saved dates)", code) from None
        changes = [
            r
            for r in state.date_changes_since(now - timedelta(days=days))
            if datetime.fromisoformat(r["start"]) >= now
        ]
        exams = upcoming_exams(state, now)
        if ics is not None:
            from .ics import to_ics

            active = {c.id for c in cfg.courses.values() if not c.exclude}
            target = ics.expanduser()
            target.write_text(
                to_ics(d for d in state.all_course_dates() if d.course_id in active),
                encoding="utf-8",
                newline="",
            )
            if not OPTS.json:
                console.print(f"[green]✓[/green] Calendar written: {esc(target)}")
    if OPTS.json:
        emit_json(
            {
                "changes": [
                    {k: r[k] for k in ("kind", "start", "text", "detected_at")} for r in changes
                ],
                "exams": [
                    {"course": d.course, "start": d.start, "end": d.end, "location": d.location}
                    for d in exams
                ],
            }
        )
        return
    console.print(f"[bold]Changed dates[/bold] [dim](found in the last {days} days)[/dim]")
    if first and not offline:
        console.print("  [dim]Watching your courses from now on; changes show up here.[/dim]")
    for r in changes:
        console.print(f"  [yellow]●[/yellow] {esc(r['text'])}")
    if not changes and not first:
        console.print("  [dim]none[/dim]")
    console.print("\n[bold]Exams[/bold]")
    for d in exams:
        until = (d.start.date() - now.date()).days
        place = f" · {d.location}" if d.location else ""
        cancelled = " [red]cancelled[/red]" if d.cancelled else ""
        console.print(
            f"  {esc(d.when())}  {esc(d.course)}{esc(place)}  [dim]in {until} days[/dim]{cancelled}"
        )
    if not exams:
        console.print("  [dim]none in Stud.IP yet (dates with the category Klausur/Prüfung)[/dim]")
