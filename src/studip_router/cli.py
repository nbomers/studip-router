"""Command line interface (`studip`). Without a command it opens the TUI."""

from __future__ import annotations

import getpass
import logging
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from . import __version__, auth, new_version, paths, report
from . import cli_accounts as cli_accounts  # register commands
from . import cli_rules as cli_rules  # register commands
from . import cli_services as cli_services  # register commands
from .api import ApiError, AuthFailed, StudipClient
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
from .config import DEFAULT_COURSE_FOLDERS, Config, ConfigError, load, unsafe_folder
from .config import parse as parse_config
from .dates import check_dates
from .fsutil import Locked
from .news import fetch_news, unread
from .notify import dates_message, notify, sync_message
from .planner import Kind
from .reorganize import apply_reorganize, apply_undo, pending_moves, plan_reorganize, plan_undo
from .report import esc
from .state import State, StateError
from .sync import (
    CourseStatus,
    build_plan,
    execute_plan,
    list_courses,
    resolve_semester,
    run_merges,
)
from .wizard import (
    CourseChoice,
    course_folder_name,
    create_course_folder,
    detect_course_folders,
    detect_default_target,
    display_path,
    find_roots,
    home_path,
    local_course_folders,
    new_root,
    propose_target,
    render_config,
)

# -- helpers ----------------------------------------------------------------------------


@app.callback()
def main_callback(
    ctx: typer.Context,
    profile: Annotated[
        str | None, typer.Option("--profile", "-p", help="Use a separate config/state profile.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    version: Annotated[bool, typer.Option("--version", help="Show the version.")] = False,
) -> None:
    if version:
        print(f"studip-router {__version__}")
        raise typer.Exit()
    try:
        paths.set_profile(profile)
        paths.profile()  # also checks STUDIP_ROUTER_PROFILE
    except ValueError as exc:
        raise fail(str(exc), 2) from None
    OPTS.json = as_json
    if ctx.invoked_subcommand is None:
        if sys.stdin.isatty() and sys.stdout.isatty() and not as_json:
            from .tui import run_tui

            run_tui()
        else:
            console.print(ctx.get_help())


# -- auth -------------------------------------------------------------------------------


# -- config -----------------------------------------------------------------------------


# -- init -------------------------------------------------------------------------------


@app.command("init")
def init_cmd(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config.")] = False,
) -> None:
    """Interactive setup: login, course list, target folders, config file."""
    path = paths.config_file()
    if path.exists() and not force:
        raise fail(f"{path} exists; edit it with `studip config edit` or use --force")
    console.print("[bold]studip-router setup[/bold]  [dim]Enter accepts the suggestion[/dim]\n")
    base_url = Prompt.ask("Stud.IP address of your university").strip().rstrip("/")
    if base_url and not base_url.startswith(("https://", "http://")):
        base_url = "https://" + base_url
    username = Prompt.ask("Username")
    password = getpass.getpass("Password (stored in the keychain): ")
    try:
        with StudipClient(base_url, username, password) as client:
            me = client.me()
            semester = client.current_semester()
            courses = [c for c in client.courses(me["id"]) if c.runs_in(semester)]
    except ApiError as exc:
        raise fail(str(exc), 3) from None
    courses.sort(key=lambda c: c.title.casefold())
    console.print(
        f"[green]✓[/green] Login works. {len(courses)} courses in {esc(semester.title)}.\n"
    )

    home = Path.home()
    with console.status("Looking for your course folders..."):
        roots = find_roots([c.title for c in courses], home)
    options = [r.path for r in roots]
    fresh = new_root(semester.title, home)
    if fresh not in options:
        options.append(fresh)
    console.print("[bold]Where do your course folders live?[/bold]")
    for i, option in enumerate(options, 1):
        found = next((r for r in roots if r.path == option), None)
        label = found.label(home, len(courses)) if found else display_path(option, home) + "  (new)"
        console.print(f"  {i}  {esc(label)}")
    answer = Prompt.ask("Number or a path", default="1").strip()
    root_path = (
        options[int(answer) - 1]
        if answer.isdigit() and 0 < int(answer) <= len(options)
        else Path(answer).expanduser()
    )
    if problem := unsafe_folder(str(root_path)):  # before anything is created there
        raise fail(f"{root_path}: the course folder location {problem}", 2)
    root = home_path(root_path, home)

    folders = local_course_folders(root_path)
    proposals = [propose_target(c.title, folders) for c in courses]
    fresh_start = not any(proposals)
    console.print(
        "\n[bold]Which courses should be synced?[/bold] [dim]Enter keeps the suggestion, "
        "'-' skips a course, any other text is the folder name.[/dim]"
    )
    choices = []
    for course, proposal in zip(courses, proposals, strict=True):
        suggestion = proposal or (course_folder_name(course.title) if fresh_start else "-")
        target = Prompt.ask(f"  [bold]{esc(course.title)}[/bold]", default=suggestion).strip()
        choices.append(
            CourseChoice(
                course.id, course.title, course.semester_title, None if target == "-" else target
            )
        )
    course_dirs = [root_path / c.target for c in choices if c.target]
    template = detect_course_folders([d for d in course_dirs if d.is_dir()]) or (
        DEFAULT_COURSE_FOLDERS
    )
    template = tuple(
        p.strip()
        for p in Prompt.ask(
            "\nSubfolders of new course folders (comma separated)", default=", ".join(template)
        ).split(",")
        if p.strip()
    )
    detected = detect_default_target([d for d in course_dirs if d.is_dir()])
    default_target = Prompt.ask(
        "Folder for files that no rule and no folder name places",
        default=detected or ("Sonstiges" if "Sonstiges" in template else template[-1]),
    )
    text = render_config(base_url, username, root, choices, default_target, template)
    try:  # valid before any folder is created or the password is stored
        parse_config(tomllib.loads(text), path)
    except ConfigError as exc:
        raise fail(f"not written: {'; '.join(exc.problems)}", 2) from None
    missing = [d for d in course_dirs if not d.is_dir()]
    if missing and Confirm.ask(
        f"Create {len(missing)} course folder(s) in {display_path(root_path, home)}, "
        f"each with {', '.join(template)}?",
        default=True,  # type: ignore[arg-type]  # rich's stub types `default` too narrowly
    ):
        for d in missing:
            create_course_folder(root_path, d.name, template)
    try:  # first, so a missing keychain leaves no half-finished setup behind
        auth.set_password(base_url, username, password)
    except auth.AuthError as exc:
        raise fail(f"{exc} - the config was not written", 3) from None
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.write_text(text, encoding="utf-8")
    paths.private_file(path)
    console.print(f"\n[green]✓[/green] Wrote {esc(path)}")
    console.print(
        "Next: [bold]studip rules suggest --write[/bold], then [bold]studip sync --dry-run[/bold]."
    )


# -- courses / sync ---------------------------------------------------------------------


@app.command("courses")
def courses_cmd(
    config: ConfigOpt = None,
    semester: Annotated[
        str | None, typer.Option("--semester", "-s", help='"current", "all" or "WiSe 2026/27".')
    ] = None,
) -> None:
    """List Stud.IP courses and whether they are synced."""
    cfg = load_config(config)
    try:
        with open_client(cfg) as client:
            sem = resolve_semester(client, semester or cfg.semester)
            entries, missing = list_courses(client, cfg, sem)
    except ApiError as exc:
        raise fail(str(exc)) from None
    if OPTS.json:
        emit_json(
            {
                "semester": sem.title if sem else "all",
                "courses": [
                    {
                        "id": e.course.id,
                        "title": e.course.title,
                        "semester": e.course.semester_title,
                        "status": e.status,
                        "target": e.config.target if e.config else None,
                    }
                    for e in entries
                ],
                "configured_but_missing": [c.id for c in missing],
            }
        )
        return
    console.print(report.courses_table(entries, sem.title if sem else "all semesters"))
    for c in missing:
        console.print(
            f"[yellow]Configured but not on Stud.IP:[/yellow] {esc(c.id)} {esc(c.name)}"
            f" [dim](remove: studip config remove-course {esc(c.id)})[/dim]"
        )


@app.command("semester")
def semester_cmd(
    config: ConfigOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Only show the plan.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Accept every suggestion.")] = False,
    keep_root: Annotated[bool, typer.Option("--keep-root", help="No new semester folder.")] = False,
    read_only: Annotated[
        bool,
        typer.Option("--read-only", help="Also make the files of archived courses read-only."),
    ] = False,
) -> None:
    """Semester change: set up the new courses, archive the old ones.

    Offers a folder for the new semester (when the root is named after one),
    a folder per new course, and stops syncing courses that have ended.
    Files already synced stay where they are.
    """
    from . import semester as sem_mod

    cfg = load_config(config)
    if OPTS.json and not (yes or dry_run):
        raise fail("with --json, add --dry-run (show the plan) or --yes (apply it)", 2)
    try:
        with open_client(cfg) as client:
            change = sem_mod.detect(client, cfg)
    except ApiError as exc:
        raise fail(str(exc)) from None
    if change is None:
        if OPTS.json:
            emit_json({"change": None})
            return
        console.print(
            "[green]✓[/green] No semester change to make: no set-up course has ended and "
            "no new semester has started. New courses mid-semester: `studip courses`."
        )
        return
    choices = sem_mod.default_choices(change)
    if keep_root:
        choices.new_root = None
        choices.add = sem_mod.new_targets(change, new_root=False)
    choices.read_only = read_only
    home = Path.home()
    interactive = not (yes or dry_run or OPTS.json)

    if not OPTS.json:
        console.print(f"[bold]Semester change to {esc(change.semester.title)}[/bold]\n")
    if interactive and change.proposed_root and not keep_root:
        answer = Prompt.ask(
            f"Folder for the new semester [dim](now {esc(display_path(cfg.root, home))};"
            " '-' keeps it)[/dim]",
            default=display_path(change.proposed_root, home),
        ).strip()
        if answer == "-":
            choices.new_root = None
        elif answer != display_path(change.proposed_root, home):
            choices.new_root = sem_mod.typed_root(cfg, answer)
        if choices.new_root != change.proposed_root:
            change.proposed_root = choices.new_root
            choices.add = sem_mod.new_targets(change, new_root=choices.new_root is not None)
    if interactive and change.new_courses:
        console.print(
            "\n[bold]New courses[/bold] [dim]Enter keeps the folder name, '-' does not sync"
            " the course[/dim]"
        )
        for course in change.new_courses:
            answer = Prompt.ask(f"  {esc(course.title)}", default=choices.add[course.id]).strip()
            if answer == "-":
                del choices.add[course.id]
                choices.skip.add(course.id)
            else:
                choices.add[course.id] = answer
    if interactive and change.old_courses:
        console.print("\n[bold]Courses from before[/bold] [dim]archived = no longer synced[/dim]")
        for old in change.old_courses:
            until = old.course.end_semester.title if old.course.end_semester else "?"
            when = "ended" if old.ended else "runs until the end of"
            if Confirm.ask(
                f"  Archive {esc(old.label)} [dim]({when} {esc(until)})[/dim]?",
                default=old.ended,
            ):
                choices.archive.add(old.config.id)
                choices.keep.discard(old.config.id)
            else:
                choices.archive.discard(old.config.id)
                choices.keep.add(old.config.id)

    target_root = choices.new_root or cfg.root
    if OPTS.json:
        data: dict[str, Any] = {
            "semester": change.semester.title,
            "root": str(target_root),
            "new_courses": [
                {"id": c.id, "title": c.title, "target": choices.add.get(c.id)}
                for c in change.new_courses
            ],
            "archive": sorted(choices.archive),
            "keep": sorted(choices.keep),
        }
    else:
        console.print("\n[bold]Plan[/bold]")
        if choices.new_root and choices.new_root != cfg.root:
            console.print(
                f"  new semester folder [b]{esc(display_path(target_root, home))}[/b]"
                " [dim](set-up courses keep their folders)[/dim]"
            )
        for course in change.new_courses:
            if course.id in choices.add:
                console.print(
                    f"  [green]+[/green] {esc(course.title)} → "
                    f"{esc(choices.add[course.id])}/ [dim]({esc(', '.join(change.subfolders))})"
                    "[/dim]"
                )
            else:
                console.print(f"  [dim]- {esc(course.title)}: not synced[/dim]")
        for old in change.old_courses:
            mark = "[yellow]archive[/yellow]" if old.config.id in choices.archive else "keep"
            console.print(f"  {mark} {esc(old.label)}")
    if dry_run:
        if OPTS.json:
            emit_json(data)
        else:
            console.print("\n[dim]Dry run: nothing changed.[/dim]")
        return
    if interactive and not Confirm.ask("\nApply?", default=True):
        raise typer.Exit(1)
    try:
        applied = sem_mod.apply_change(cfg, change, choices)
    except (ConfigError, ValueError, OSError) as exc:
        raise fail(f"nothing changed: {exc}", 2) from None
    if OPTS.json:
        data["created"] = [str(p) for p in applied.created]
        data["read_only_files"] = applied.read_only_files
        data["problems"] = applied.problems
        emit_json(data)
        return
    console.print(f"\n[green]✓[/green] Config updated (backup: {esc(cfg.path)}.bak)")
    for folder in applied.created:
        console.print(f"  [green]✓[/green] created {esc(display_path(folder, home))}")
    if applied.read_only_files:
        console.print(
            f"  {applied.read_only_files} file(s) made read-only"
            " [dim](undo: chmod -R u+w FOLDER)[/dim]"
        )
    for problem in applied.problems:
        console.print(f"  [red]✗[/red] {esc(problem)}")
    if choices.add:
        console.print(
            "Next: [bold]studip rules suggest --write[/bold], then"
            " [bold]studip sync --dry-run[/bold]."
        )
    if applied.problems:
        raise typer.Exit(1)


@app.command("sync")
def sync_cmd(
    config: ConfigOpt = None,
    course: CourseOpt = None,
    semester: Annotated[
        str | None,
        typer.Option("--semester", "-s", help='"current", "all" or "WiSe 2026/27".'),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Only show the plan.")] = False,
    include_large: Annotated[
        bool, typer.Option("--include-large", help="Ignore the size limit for this run.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Also list skips.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only print problems.")] = False,
    send_notification: Annotated[
        bool, typer.Option("--notify", help="Desktop notification about new files.")
    ] = False,
) -> None:
    """Download new and changed files according to the rules."""
    cfg = load_config(config)
    report.setup_file_logging(paths.log_file(), verbose)
    out = Console(quiet=quiet or OPTS.json, highlight=False)
    background = send_notification  # the scheduler runs `sync --notify --quiet`
    pause = paths.auth_pause_file()
    if background and pause.exists():
        # A failed login paused auto-sync so a wrong password cannot lock the account.
        log_line = "auto-sync paused after a failed login; run `studip auth login`"
        logging.getLogger("studip_router").warning(log_line)
        raise typer.Exit(3)
    if background:
        from . import update_hold

        with State(paths.state_db()) as state:
            held = update_hold.hold_background(cfg, state)
        if held is not None:
            notice, first = held
            logging.getLogger("studip_router").warning(update_hold.message(notice))
            if first:
                notify("studip-router", update_hold.message(notice))
            return
    try:
        password = auth.get_password(cfg.base_url, cfg.username)
    except auth.AuthError as exc:
        if background:
            notify("studip-router", "No password stored - run `studip auth login`.")
        raise fail(str(exc), 3) from None
    with (
        StudipClient(cfg.base_url, cfg.username, password) as client,
        State(paths.state_db()) as state,
    ):
        try:
            with out.status("Fetching course data..."):
                sync_plan = build_plan(
                    client,
                    cfg,
                    state,
                    semester_name=semester,
                    course_query=course,
                    include_large=include_large,
                )
        except AuthFailed as exc:
            _pause_auto_sync(background)
            raise fail(f"{exc}; auto-sync is paused until `studip auth login`", 3) from None
        except ApiError as exc:
            if background:
                notify("studip-router", f"Sync failed: {exc}")
            raise fail(str(exc)) from None
        pause.unlink(missing_ok=True)  # login works again
        if not background:  # a sync by hand shows the plan: the update has been seen
            from .update_hold import mark_seen

            mark_seen()
        if dry_run or verbose:
            for plan in sync_plan.plans:
                if (table := report.plan_table(plan, verbose)) is not None:
                    out.print(report.plan_heading(plan), soft_wrap=True)
                    out.print(table)
        result = None
        if not dry_run:
            try:
                result = execute_plan(
                    sync_plan,
                    cfg,
                    state,
                    client,
                    on_course=lambda p: out.print(
                        f"[bold]{esc(p.course.name or p.course.id)}[/bold]"
                    ),
                    on_progress=lambda a: out.print(
                        f"  [{report.STYLE[a.kind]}]{report.SYMBOL[a.kind]}[/] "
                        + esc(a.dest.name if a.dest else "")
                    ),
                    on_failed=lambda a, msg: out.print(
                        f"  [red]✗[/] {esc(a.dest.name if a.dest else '')}  [dim]{esc(msg)}[/]"
                    ),
                )
            except Locked as exc:
                raise fail(f"{exc} - try again in a moment", 75) from None
            except AuthFailed as exc:
                _pause_auto_sync(background)
                raise fail(f"{exc}; auto-sync is paused until `studip auth login`", 3) from None
            except KeyboardInterrupt:
                err.print(
                    "\n[yellow]Interrupted.[/yellow] Finished files are recorded, no "
                    "partial files were left. Run sync again to continue."
                )
                raise typer.Exit(130) from None
        news_count = _news_after_sync(client, cfg, state, sync_plan) if not dry_run else 0
        date_changes = (
            check_dates(client, state, _plan_courses(sync_plan))
            if cfg.watch_dates and not dry_run
            else []
        )
        sync_plan.warnings += [w for w in client.warnings if w not in sync_plan.warnings]
        to_move = len(pending_moves(cfg, state)) if not dry_run else 0

        if OPTS.json:
            data = report.plan_json(sync_plan, result, dry_run, news_count, date_changes)
            data["reorganize_pending"] = to_move
            emit_json(data)
        else:
            report.print_summary(out, sync_plan, dry_run, result.outcome if result else None)
            if result:
                report.print_unlocked(out, result.outcome)
            for m in result.merges if result else []:
                if m.written:
                    out.print(
                        f"  [green]✓[/green] merged slides: {esc(m.output)} ({esc(m.reason)})"
                    )
                elif not m.reason.startswith("up to date"):
                    out.print(f"  [yellow]◎[/yellow] merge {esc(m.course)}: {esc(m.reason)}")
            if news_count:
                out.print(f"\n[cyan]{news_count} new announcement(s)[/cyan] - `studip news`")
            report.print_date_changes(out, date_changes)
            if to_move:
                out.print(f"\n{report.reorganize_hint(to_move)}")
            if quiet:
                for name, problem in sync_plan.errors:
                    err.print(f"[red]✗[/red] course {esc(name)}: {esc(problem)}")
                for action, msg in result.outcome.failed if result else []:
                    err.print(
                        f"[red]✗[/red] {esc(action.file.name if action.file else '')}: {esc(msg)}"
                    )
                for action in (a for p in sync_plan.plans for a in p.actions):
                    if action.kind is Kind.ERROR:
                        name = action.file.name if action.file else ""
                        err.print(f"[red]✗[/red] {esc(name)}: {esc(action.reason)}")
        failed = len(result.outcome.failed) if result else 0
        # Files the plan already knew it could not place ("target folder does not
        # exist", a rename that fails, on_conflict = "error") are problems too.
        failed += sum(1 for p in sync_plan.plans for a in p.actions if a.kind is Kind.ERROR)
        if background and cfg.notify:
            message = sync_message(result.new_by_course(sync_plan) if result else {}, news_count)
            problems = failed + len(sync_plan.errors)
            if problems:
                message = (message + ". " if message else "") + (
                    f"{problems} problem(s) - open studip for details"
                )
            if message:
                notify("Stud.IP", message)
            if dates := dates_message([c.text() for c in date_changes]):
                notify("Stud.IP dates", dates)
        if failed or sync_plan.errors:
            raise typer.Exit(1)  # planned errors included


def _pause_auto_sync(background: bool) -> None:
    """A rejected login pauses auto-sync, so a changed password cannot lock the account."""
    pause = paths.auth_pause_file()
    pause.parent.mkdir(parents=True, exist_ok=True)
    pause.write_text("login failed\n")
    if background:
        notify("studip-router", "Stud.IP login failed - auto-sync paused. Run `studip auth login`.")


def _plan_courses(sync_plan) -> list[tuple[str, str]]:
    return [(p.course.id, p.course.name or p.course.target) for p in sync_plan.plans]


def _news_after_sync(client: StudipClient, cfg: Config, state: State, sync_plan) -> int:
    """Count unread announcements of the synced courses (never fails the sync)."""
    try:
        items = [
            n
            for p in sync_plan.plans
            for n in fetch_news(client, p.course.id, p.course.name or p.course.target)
        ]
    except ApiError:
        return 0
    return len(unread(state, items))


@app.command("status")
def status_cmd(
    config: ConfigOpt = None,
    offline: Annotated[bool, typer.Option("--offline", help="Do not contact Stud.IP.")] = False,
) -> None:
    """Open issues: unconfigured courses, default-routed files, last run."""
    cfg = load_config(config)
    with State(paths.state_db()) as state:
        last = state.last_run()
        default_routed = state.default_routed()
        to_move = pending_moves(cfg, state)
    unconfigured: list[dict] = []
    if not offline:
        try:
            with open_client(cfg) as client:
                sem = resolve_semester(client, cfg.semester)
                entries, _ = list_courses(client, cfg, sem)
            unconfigured = [
                {"id": e.course.id, "title": e.course.title}
                for e in entries
                if e.status is CourseStatus.UNCONFIGURED
            ]
        except (ApiError, typer.Exit) as exc:
            if isinstance(exc, ApiError):
                err.print(f"[yellow]Could not reach Stud.IP:[/yellow] {esc(exc)}")
    newer = None
    if cfg.update_check:
        newer = new_version.cached_newer() if offline else new_version.check()
    data = {
        "newer_version": newer,
        "last_run": dict(last) if last else None,
        "default_routed": [
            {"path": r.local_path, "remote": f"{r.remote_folder_path}/{r.remote_name}".lstrip("/")}
            for r in default_routed
        ],
        "unconfigured_courses": unconfigured,
        "reorganize_pending": [{"from": str(m.src), "to": str(m.dst)} for m in to_move],
    }
    if OPTS.json:
        emit_json(data)
        return
    if last:
        console.print(
            f"Last run: {esc(last['mode'])} at {esc(last['finished_at'])} - {esc(last['summary'])}"
        )
    else:
        console.print("No sync has run yet.")
    if unconfigured:
        console.print("\n[yellow]Courses without config entry:[/yellow]")
        for c in unconfigured:
            console.print(f"  {esc(c['id'])}  {esc(c['title'])}")
    if default_routed:
        console.print(
            f"\n[yellow]{esc(len(default_routed))} file(s) only routed by the default "
            "target[/yellow] - add rules (`studip rules suggest`):"
        )
        for r in default_routed:
            remote = esc(f"{r.remote_folder_path}/{r.remote_name}")
            console.print(f"  {esc(r.local_path)}  [dim]← {remote}[/dim]")
    if to_move:
        console.print(f"\n{report.reorganize_hint(len(to_move))}")
    if newer:
        console.print(f"\n[cyan]{esc(new_version.hint(newer))}[/cyan]")
    if not unconfigured and not default_routed and not to_move:
        console.print("[green]✓[/green] Nothing to do.")


# -- rules ------------------------------------------------------------------------------


# -- reorganize / undo ------------------------------------------------------------------


@app.command("reorganize")
def reorganize_cmd(
    course: CourseOpt = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", "-n", help="Only show what would move.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before moving.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Move already synced files to where the current rules would put them."""
    cfg = load_config(config)
    with State(paths.state_db()) as state:
        moves = plan_reorganize(cfg, state, find_course(cfg, course).id if course else None)
        if OPTS.json and dry_run:
            emit_json([{"from": m.src, "to": m.dst, "problem": m.problem} for m in moves])
            return
        if not moves:
            console.print("[green]✓[/green] Everything is where the rules want it.")
            return
        table = Table(title="Reorganize", title_justify="left")
        for col in ("from", "to", "note"):
            table.add_column(col)
        for m in moves:
            base = cfg.root
            table.add_row(
                Text(_rel(m.src, base)), Text(_rel(m.dst, base)), Text(m.problem, "yellow")
            )
        console.print(table)
        movable = [m for m in moves if not m.problem]
        if dry_run or not movable:
            return
        if not yes and not Confirm.ask(f"Move {len(movable)} file(s)? (undo: studip undo)"):
            raise typer.Exit()
        done, failed = apply_reorganize(moves, state)
        console.print(f"[green]✓[/green] Moved {done} file(s).")
        for m, msg in failed:
            err.print(f"[red]✗[/red] {esc(m.src.name)}: {esc(msg)}")


@app.command("undo")
def undo_cmd(
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Do not ask before moving back.")
    ] = False,
) -> None:
    """Revert the last `studip reorganize` (downloads are never deleted)."""
    with State(paths.state_db()) as state:
        run_id, steps = plan_undo(state)
        moves = [s for s in steps if s.op == "move"]
        if run_id is None or not moves:
            console.print("Nothing to undo.")
            return
        for s in moves:
            note = f" [yellow]({s.problem})[/yellow]" if s.problem else ""
            console.print(f"  {esc(s.dst)}\n    → {esc(s.src)}{note}")
        if not yes and not Confirm.ask(f"Move {len(moves)} file(s) back?"):
            raise typer.Exit()
        console.print(f"[green]✓[/green] Reverted {esc(apply_undo(state, run_id, steps))} move(s).")


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# -- history / news / merge -------------------------------------------------------------


@app.command("open")
def open_cmd(
    course: Annotated[str, typer.Argument(help="Course ID, title or target (substring).")],
    config: ConfigOpt = None,
) -> None:
    """Open a course folder in Finder or the file manager."""
    cfg = load_config(config)
    folder = cfg.course_dir(find_course(cfg, course))
    if not folder.is_dir():
        raise fail(f"{folder} does not exist yet - sync first", 1)
    if OPTS.json:
        emit_json({"path": str(folder)})
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if not shutil.which(opener):
        console.print(esc(folder))  # no file manager (server, SSH): the path at least
        return
    subprocess.run([opener, str(folder)], check=False)


@app.command("history")
def history_cmd(
    limit: Annotated[int, typer.Option("--limit", "-l", help="How many files to show.")] = 30,
    old_limit: Annotated[int | None, typer.Option("-n", hidden=True)] = None,
) -> None:
    """Files that arrived most recently."""
    if old_limit is not None:  # before 0.4.2; -n means --dry-run everywhere else
        err.print("[yellow]Note:[/yellow] `history -n` is now `history -l` (--limit)")
        limit = old_limit
    with State(paths.state_db()) as state:
        records = state.recent_files(limit)
    if OPTS.json:
        emit_json(
            [
                {
                    "path": r.local_path,
                    "synced_at": r.synced_at,
                    "course_id": r.course_id,
                    "version": r.version,
                }
                for r in records
            ]
        )
        return
    if not records:
        console.print("Nothing synced yet.")
        return
    table = Table(title="Recently synced", title_justify="left")
    for col in ("when", "file", "folder"):
        table.add_column(col)
    for r in records:
        p = Path(r.local_path)
        table.add_row(r.synced_at.replace("T", " ")[:16], Text(p.name), Text(str(p.parent)))
    console.print(table)


@app.command("news")
def news_cmd(
    course: CourseOpt = None,
    show_all: Annotated[bool, typer.Option("--all", help="Include read announcements.")] = False,
    keep_unread: Annotated[
        bool, typer.Option("--keep-unread", help="Do not mark the shown ones as read.")
    ] = False,
    config: ConfigOpt = None,
) -> None:
    """Show (unread) course announcements."""
    cfg = load_config(config)
    courses = (
        [find_course(cfg, course)] if course else [c for c in cfg.courses.values() if not c.exclude]
    )
    with open_client(cfg) as client, State(paths.state_db()) as state:
        items = []
        for c in courses:
            try:
                items += fetch_news(client, c.id, c.name or c.target)
            except AuthFailed as exc:  # do not repeat a rejected login for every course
                raise fail(str(exc), 3) from None
            except ApiError as exc:
                err.print(f"[yellow]{esc(c.name)}: {esc(exc)}[/yellow]")
        shown = items if show_all else unread(state, items)
        if OPTS.json:
            emit_json(shown)
        elif not shown:
            console.print("No unread announcements.")
        for n in shown if not OPTS.json else []:
            when = n.published.strftime("%d.%m.%Y") if n.published else ""
            console.rule(
                f"[bold]{esc(n.title)}[/bold]  [dim]{esc(n.course)} · {esc(when)}[/dim]",
                align="left",
            )
            console.print(Text(n.text + "\n"))
        if not keep_unread:
            state.mark_news_seen([(n.id, n.course_id) for n in shown])


@app.command("merge")
def merge_cmd(
    course: CourseOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Download iCloud-only PDFs.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Merge lecture PDFs into one file per course (set up in the UI: Merge slides)."""
    cfg = load_config(config)
    courses = [find_course(cfg, course)] if course else list(cfg.courses.values())
    with State(paths.state_db()) as state:
        results = run_merges(cfg, state, courses, force=force)
    if OPTS.json:
        emit_json(results)
        return
    if not results:
        console.print(
            "No course has a merge setting, e.g.\n  merge = { source = "
            '"Skript : Alle Folien", output = "Alle Folien.pdf" }'
        )
    for m in results:
        mark = "[green]✓[/green]" if m.written else "[dim]◎[/dim]"
        console.print(f"{mark} {esc(m.course)}: {esc(m.reason)}  [dim]{esc(m.output)}[/dim]")


# -- schedule ---------------------------------------------------------------------------


# -- doctor -----------------------------------------------------------------------------


# -- course dates -----------------------------------------------------------------------


# -- PDF passwords ----------------------------------------------------------------------


@app.command("_complete", hidden=True)
def complete_cmd(what: str) -> None:
    """Offline values for shell completion: courses | profiles."""
    if what == "profiles":
        print("\n".join(paths.list_profiles()))
        return
    try:
        cfg = load(paths.config_file())
    except ConfigError:
        return
    for c in cfg.courses.values():
        if not c.exclude:
            label = (c.name or c.target).replace("\t", " ")
            print(f"{c.target}\t{label}")


@app.command("completion")
def completion_cmd(
    shell: Annotated[str, typer.Argument(help="zsh, bash or fish.")],
) -> None:
    """Print the completion script for a shell (see the README for where it goes)."""
    from importlib.resources import files

    if shell not in ("zsh", "bash", "fish"):
        raise fail(f"unknown shell {shell!r}: use zsh, bash or fish", 2)
    sys.stdout.write((files("studip_router") / "completions" / shell).read_text("utf-8"))


@app.command("tui")
def tui_cmd() -> None:
    """Open the interactive terminal UI (same as running `studip` without a command)."""
    from .tui import run_tui

    run_tui()


def hoist_global_options(args: list[str]) -> list[str]:
    """Move ``--json`` and ``-p/--profile`` from after the command to the front.

    Click only accepts them before the command; ``studip sync --json`` failing with
    "no such option" surprised people. Everything after ``--`` stays as it is.
    """
    head: list[str] = []
    rest: list[str] = []
    seen_command = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            rest += args[i:]
            break
        if not seen_command:
            if arg in ("-p", "--profile") and i + 1 < len(args):
                head += args[i : i + 2]
                i += 2
                continue
            if not arg.startswith("-"):
                seen_command = True
                rest.append(arg)
            else:
                head.append(arg)
        elif arg == "--json" or arg.startswith("--profile="):
            head.append(arg)
        elif arg in ("-p", "--profile") and i + 1 < len(args):
            head += args[i : i + 2]
            i += 2
            continue
        else:
            rest.append(arg)
        i += 1
    return head + rest


def main() -> None:
    """Entry point; expected problems end with a message and exit code, no traceback."""
    try:
        app(args=hoist_global_options(sys.argv[1:]))
    except (StateError, auth.AuthError) as exc:
        code = 3 if isinstance(exc, auth.AuthError) else 4
        if OPTS.json:
            emit_json({"error": str(exc)})
        else:
            err.print(f"[red]Error:[/red] {esc(exc)}")
        sys.exit(code)
