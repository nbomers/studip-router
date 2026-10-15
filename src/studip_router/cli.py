"""Command line interface (`studip`). Without a command it opens the TUI."""

from __future__ import annotations

import getpass
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__, auth, doctor, paths, report, schedule
from .api import ApiError, AuthFailed, StudipClient
from .config import Config, ConfigError, CourseConfig, load
from .dates import check_dates, upcoming_exams
from .executor import unlock_course
from .fsutil import Locked, exclusive_lock
from .models import RemoteFile
from .news import fetch_news, unread
from .notify import dates_message, notify, sync_message
from .planner import build_router
from .rename import RenameError
from .reorganize import apply_reorganize, apply_undo, pending_moves, plan_reorganize, plan_undo
from .state import State
from .suggest import suggest_rules
from .sync import (
    CourseStatus,
    build_plan,
    execute_plan,
    list_courses,
    resolve_semester,
    run_merges,
)
from .tomlwrite import append_blocks, rule_block
from .wizard import (
    CourseChoice,
    detect_default_target,
    local_course_folders,
    propose_target,
    render_config,
)

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Sync Stud.IP course files into your own folders, with routing and renaming rules.",
    rich_markup_mode="rich",
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


# -- helpers ----------------------------------------------------------------------------


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
        err.print(f"[red]Error:[/red] {message}")
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


@auth_app.command("login")
def auth_login(config: ConfigOpt = None) -> None:
    """Ask for the password, verify it, store it in the OS keychain."""
    cfg = load_config(config)
    password = getpass.getpass(f"Password for {cfg.username} at {cfg.base_url}: ")
    try:
        with StudipClient(cfg.base_url, cfg.username, password) as client:
            me = client.me()
    except AuthFailed as exc:
        raise fail(f"{exc} - nothing was stored", 3) from None
    except ApiError as exc:
        raise fail(str(exc)) from None
    auth.set_password(cfg.base_url, cfg.username, password)
    paths.auth_pause_file().unlink(missing_ok=True)  # resume auto-sync
    console.print(
        f"[green]✓[/green] Logged in as {me['name'] or cfg.username}; "
        "password stored in the keychain."
    )


@auth_app.command("status")
def auth_status(config: ConfigOpt = None) -> None:
    """Check whether a password is stored and works."""
    cfg = load_config(config)
    if not auth.has_password(cfg.base_url, cfg.username):
        raise fail("no password stored; run `studip auth login`", 3)
    try:
        with open_client(cfg) as client:
            client.me()
    except ApiError as exc:
        raise fail(f"stored password does not work: {exc}", 3) from None
    console.print("[green]✓[/green] Stored password works.")


@auth_app.command("logout")
def auth_logout(config: ConfigOpt = None) -> None:
    """Remove the stored password from the keychain."""
    cfg = load_config(config)
    removed = auth.delete_password(cfg.base_url, cfg.username)
    console.print("Password removed." if removed else "No password was stored.")


# -- config -----------------------------------------------------------------------------


@config_app.command("path")
def config_path() -> None:
    """Print the config file location."""
    print(paths.config_file())


@config_app.command("validate")
def config_validate(config: ConfigOpt = None) -> None:
    """Validate the config file."""
    cfg = load_config(config)
    active = sum(1 for c in cfg.courses.values() if not c.exclude)
    rules = sum(len(c.rules) for c in cfg.courses.values())
    if OPTS.json:
        emit_json({"path": cfg.path, "active": active, "rules": rules, "valid": True})
        return
    console.print(
        f"[green]✓[/green] {cfg.path}: {active} active, "
        f"{len(cfg.courses) - active} excluded courses, {rules} rules"
    )


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. sync.on_update")],
    value: Annotated[str, typer.Argument(help='TOML value: skip, true, 200, "text"')],
) -> None:
    """Change one setting in place (comments are kept; the result is validated)."""
    import tomllib

    from .config_edit import set_value

    try:
        parsed = tomllib.loads(f"v = {value}")["v"]
    except tomllib.TOMLDecodeError:
        parsed = value  # bare words are strings: `studip config set sync.on_update skip`
    try:
        set_value(paths.config_file(), tuple(key.split(".")), parsed)
    except ConfigError as exc:
        raise fail(f"not changed: {'; '.join(exc.problems)}", 2) from None
    console.print(f"[green]✓[/green] {key} = {parsed!r}")


@config_app.command("edit")
def config_edit() -> None:
    """Open the config in $EDITOR and validate it afterwards."""
    path = paths.config_file()
    if not path.exists():
        raise fail(f"{path} does not exist; run `studip init` first")
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
    subprocess.run([*editor.split(), str(path)], check=False)
    config_validate(None)


# -- init -------------------------------------------------------------------------------


@app.command("init")
def init_cmd(
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config.")] = False,
) -> None:
    """Interactive setup: login, course list, target folders, config file."""
    path = paths.config_file()
    if path.exists() and not force:
        raise fail(f"{path} exists; edit it with `studip config edit` or use --force")
    console.print("[bold]studip-router setup[/bold]\n")
    base_url = Prompt.ask("Stud.IP URL", default="https://studip.example.edu").rstrip("/")
    username = Prompt.ask("Username")
    password = getpass.getpass("Password (stored in the keychain): ")
    try:
        with StudipClient(base_url, username, password) as client:
            me = client.me()
            semester = client.current_semester()
            courses = [c for c in client.courses(me["id"]) if c.runs_in(semester)]
    except ApiError as exc:
        raise fail(str(exc), 3) from None
    console.print(f"[green]✓[/green] Login works. {len(courses)} courses in {semester.title}.\n")
    root = Prompt.ask(
        "Folder that contains your course folders", default=str(Path("~/Uni").expanduser())
    )
    root_path = Path(root).expanduser()
    folders = local_course_folders(root_path)
    choices = []
    for course in sorted(courses, key=lambda c: c.title.casefold()):
        proposal = propose_target(course.title, folders) or ""
        answer = Prompt.ask(
            f"[bold]{course.title}[/bold]\n  target folder ('-' to exclude)",
            default=proposal or "-",
        )
        choices.append(
            CourseChoice(
                course.id,
                course.title,
                course.semester_title,
                None if answer.strip() == "-" else answer.strip(),
            )
        )
    course_dirs = [root_path / c.target for c in choices if c.target]
    detected = detect_default_target(course_dirs)
    default_target = Prompt.ask(
        "Folder for files no rule matches (inside each course folder)",
        default=detected or "_unsorted",
    )
    text = render_config(base_url, username, root, choices, default_target)
    missing = [d for d in course_dirs if not d.is_dir()]
    if missing and Confirm.ask(
        f"Create {len(missing)} missing course folder(s)? "
        + ", ".join(d.name for d in missing[:5])
        + (" ..." if len(missing) > 5 else ""),
        default=True,
    ):
        for d in missing:
            d.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    path.write_text(text, encoding="utf-8")
    auth.set_password(base_url, username, password)
    console.print(f"\n[green]✓[/green] Wrote {path}")
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
        console.print(f"[yellow]Configured but not on Stud.IP:[/yellow] {c.id} {c.name}")


@app.command("sync")
def sync_cmd(
    config: ConfigOpt = None,
    course: CourseOpt = None,
    semester: Annotated[str | None, typer.Option("--semester", "-s")] = None,
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
            pause.parent.mkdir(parents=True, exist_ok=True)
            pause.write_text("login failed\n")
            if background:
                notify(
                    "studip-router",
                    "Stud.IP login failed - auto-sync paused. Run `studip auth login`.",
                )
            raise fail(f"{exc}; auto-sync is paused until `studip auth login`", 3) from None
        except ApiError as exc:
            if background:
                notify("studip-router", f"Sync failed: {exc}")
            raise fail(str(exc)) from None
        pause.unlink(missing_ok=True)  # login works again
        if dry_run or verbose:
            for plan in sync_plan.plans:
                if (table := report.plan_table(plan, verbose)) is not None:
                    out.print(table)
        result = None
        if not dry_run:
            try:
                result = execute_plan(
                    sync_plan,
                    cfg,
                    state,
                    client,
                    on_course=lambda p: out.print(f"[bold]{p.course.name or p.course.id}[/bold]"),
                    on_progress=lambda a: out.print(
                        f"  [{report.STYLE[a.kind]}]{report.SYMBOL[a.kind]}[/] {a.dest.name}"
                    ),
                )
            except Locked as exc:
                raise fail(f"{exc} - try again in a moment", 75) from None
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
                    out.print(f"  [green]✓[/green] merged slides: {m.output} ({m.reason})")
                elif not m.reason.startswith("up to date"):
                    out.print(f"  [yellow]◎[/yellow] merge {m.course}: {m.reason}")
            if news_count:
                out.print(f"\n[cyan]{news_count} new announcement(s)[/cyan] - `studip news`")
            report.print_date_changes(out, date_changes)
            if to_move:
                out.print(f"\n{report.reorganize_hint(to_move)}")
            if quiet:
                for name, problem in sync_plan.errors:
                    err.print(f"[red]✗[/red] course {name}: {problem}")
                for action, msg in result.outcome.failed if result else []:
                    err.print(f"[red]✗[/red] {action.file.name if action.file else ''}: {msg}")
        failed = len(result.outcome.failed) if result else 0
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
            raise typer.Exit(1)


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
                err.print(f"[yellow]Could not reach Stud.IP:[/yellow] {exc}")
    data = {
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
        console.print(f"Last run: {last['mode']} at {last['finished_at']} - {last['summary']}")
    else:
        console.print("No sync has run yet.")
    if unconfigured:
        console.print("\n[yellow]Courses without config entry:[/yellow]")
        for c in unconfigured:
            console.print(f"  {c['id']}  {c['title']}")
    if default_routed:
        console.print(
            f"\n[yellow]{len(default_routed)} file(s) only routed by the default "
            "target[/yellow] - add rules (`studip rules suggest`):"
        )
        for r in default_routed:
            console.print(f"  {r.local_path}  [dim]← {r.remote_folder_path}/{r.remote_name}[/dim]")
    if to_move:
        console.print(f"\n{report.reorganize_hint(len(to_move))}")
    if not unconfigured and not default_routed and not to_move:
        console.print("[green]✓[/green] Nothing to do.")


# -- rules ------------------------------------------------------------------------------


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
                    {"id": r.id, "target": r.target, "rename": r.rename, "skip": r.skip}
                    for r in c.rules
                ]
                for c in courses
            }
        )
        return
    for c in courses:
        table = Table(title=f"{c.name or c.id} → {c.target}", title_justify="left")
        for col in ("#", "id", "match", "target", "rename"):
            table.add_column(col)
        for i, r in enumerate(c.rules, 1):
            m = r.match
            parts = [
                f"{k}={v}"
                for k, v in (
                    ("folder", m.folder),
                    ("glob", m.name_glob),
                    ("regex", m.name_regex.pattern if m.name_regex else None),
                    ("ext", ",".join(m.ext) or None),
                    ("mime", ",".join(m.mime) or None),
                )
                if v is not None
            ]
            table.add_row(
                str(i),
                r.id,
                " ".join(parts) or "*",
                "[dim]skip[/dim]" if r.skip else str(r.target),
                r.rename or "",
            )
        table.add_row("", "[dim]default[/dim]", "*", cfg.course_default_target(c), "")
        console.print(table)


@rules_app.command("test")
def rules_test(
    course: str,
    filename: str,
    folder: Annotated[str, typer.Option("--folder", "-f", help="Stud.IP folder path.")] = "",
    size_mb: Annotated[float, typer.Option("--size", help="File size in MB.")] = 0.1,
    mime: Annotated[str, typer.Option("--mime")] = "application/octet-stream",
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
        console.print(f"Rule [bold]{route.rule_id}[/bold]: [dim]skipped[/dim]")
    else:
        console.print(f"Rule [bold]{route.rule_id or 'default target'}[/bold]\n  → {dest}")


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
            except ApiError as exc:
                err.print(f"[yellow]{c.name}: {exc}[/yellow]")
                continue
            existing = {r.id for r in c.rules}
            known_folders = {r.match.folder for r in c.rules if r.match.folder}
            suggestions = [
                s
                for s in suggest_rules(files, cfg.course_dir(c), existing)
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
        if not yes and not Confirm.ask(f"Append {len(blocks)} rule(s) to {cfg.path}?"):
            raise typer.Exit()
        backup = append_blocks(cfg.path, blocks, "rules suggested by `studip rules suggest`")
        console.print(f"[green]✓[/green] Appended. Backup: {backup}")
        load_config(cfg.path)


# -- reorganize / undo ------------------------------------------------------------------


@app.command("reorganize")
def reorganize_cmd(
    course: CourseOpt = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
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
            table.add_row(_rel(m.src, base), _rel(m.dst, base), f"[yellow]{m.problem}[/]")
        console.print(table)
        movable = [m for m in moves if not m.problem]
        if dry_run or not movable:
            return
        if not yes and not Confirm.ask(f"Move {len(movable)} file(s)? (undo: studip undo)"):
            raise typer.Exit()
        done, failed = apply_reorganize(moves, state)
        console.print(f"[green]✓[/green] Moved {done} file(s).")
        for m, msg in failed:
            err.print(f"[red]✗[/red] {m.src.name}: {msg}")


@app.command("undo")
def undo_cmd(yes: Annotated[bool, typer.Option("--yes", "-y")] = False) -> None:
    """Revert the last `studip reorganize` (downloads are never deleted)."""
    with State(paths.state_db()) as state:
        run_id, steps = plan_undo(state)
        moves = [s for s in steps if s.op == "move"]
        if run_id is None or not moves:
            console.print("Nothing to undo.")
            return
        for s in moves:
            note = f" [yellow]({s.problem})[/yellow]" if s.problem else ""
            console.print(f"  {s.dst}\n    → {s.src}{note}")
        if not yes and not Confirm.ask(f"Move {len(moves)} file(s) back?"):
            raise typer.Exit()
        console.print(f"[green]✓[/green] Reverted {apply_undo(state, run_id, steps)} move(s).")


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# -- history / news / merge -------------------------------------------------------------


@app.command("history")
def history_cmd(limit: Annotated[int, typer.Option("--limit", "-n")] = 30) -> None:
    """Files that arrived most recently."""
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
        table.add_row(r.synced_at.replace("T", " ")[:16], p.name, str(p.parent))
    console.print(table)


@app.command("news")
def news_cmd(
    course: CourseOpt = None,
    show_all: Annotated[bool, typer.Option("--all", help="Include read announcements.")] = False,
    keep_unread: Annotated[bool, typer.Option("--keep-unread")] = False,
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
            except ApiError as exc:
                err.print(f"[yellow]{c.name}: {exc}[/yellow]")
        shown = items if show_all else unread(state, items)
        if OPTS.json:
            emit_json(shown)
        elif not shown:
            console.print("No unread announcements.")
        for n in shown if not OPTS.json else []:
            when = n.published.strftime("%d.%m.%Y") if n.published else ""
            console.rule(f"[bold]{n.title}[/bold]  [dim]{n.course} · {when}[/dim]", align="left")
            console.print(n.text + "\n")
        if not keep_unread:
            state.mark_news_seen([(n.id, n.course_id) for n in shown])


@app.command("merge")
def merge_cmd(
    course: CourseOpt = None,
    force: Annotated[bool, typer.Option("--force", help="Download iCloud-only PDFs.")] = False,
    config: ConfigOpt = None,
) -> None:
    """Merge lecture PDFs into one file per course (needs `merge` in the course config)."""
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
        console.print(f"{mark} {m.course}: {m.reason}  [dim]{m.output}[/dim]")


# -- schedule ---------------------------------------------------------------------------


@schedule_app.command("install")
def schedule_install(
    every: Annotated[int, typer.Option("--every", help="Interval in minutes.")] = 120,
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    """Run `studip sync --notify` periodically (launchd on macOS, systemd on Linux)."""
    if every < 15:
        raise fail("please use an interval of at least 15 minutes")
    exe = schedule.executable()
    if warning := schedule.executable_warning(exe):
        err.print(f"[yellow]Warning:[/yellow] {warning}")
    if not yes and not Confirm.ask(
        f"Install a background job running `{exe} sync` every {every} minutes?"
    ):
        raise typer.Exit()
    try:
        where = schedule.install(every, paths.profile())
    except (OSError, subprocess.CalledProcessError) as exc:
        raise fail(f"could not install: {exc}") from None
    console.print(f"[green]✓[/green] Installed ({where}). Remove with `studip schedule uninstall`.")


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
    console.print(f"Installed ({every}{'loaded' if st.loaded else 'not loaded'}): {st.path}")


# -- doctor -----------------------------------------------------------------------------


@app.command("doctor")
def doctor_cmd(
    offline: Annotated[bool, typer.Option("--offline")] = False,
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
            console.print(f"{marks[c.level]} [bold]{c.name}[/bold]: {c.detail}")
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
        console.print(f"{r['file_id']}  {r['name']}  [dim]since {r['since'][:10]}[/dim]")


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
            with open_client(cfg) as client:
                for c in cfg.courses.values():
                    if c.exclude:
                        continue
                    hits += [
                        (f.id, c.id, f.name)
                        for f in client.files(c.id)
                        if f.id == query or q in f.name.casefold()
                    ]
        if len(hits) != 1:
            names = "\n  ".join(f"{fid}  {name}" for fid, _, name in hits[:20])
            raise fail(
                f"{len(hits)} files match {query!r}" + (f"; use the id:\n  {names}" if hits else "")
            )
        fid, cid, name = hits[0]
        state.ignore(fid, cid, name)
    console.print(f"[green]✓[/green] {name} will not be downloaded any more.")


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
    console.print(f"[green]✓[/green] {hits[0]['name']} is synced again.")


# -- course dates -----------------------------------------------------------------------


@app.command("dates")
def dates_cmd(
    config: ConfigOpt = None,
    offline: Annotated[bool, typer.Option("--offline", help="Do not contact Stud.IP.")] = False,
    days: Annotated[int, typer.Option("--days", help="Changes found in the last N days.")] = 14,
) -> None:
    """Cancelled, moved or re-roomed course dates, and upcoming exams."""
    cfg = load_config(config)
    now = datetime.now().astimezone()
    with State(paths.state_db()) as state:
        first = not state.all_course_dates()
        if not offline:
            courses = [(c.id, c.name or c.target) for c in cfg.courses.values() if not c.exclude]
            with open_client(cfg) as client:
                check_dates(client, state, courses, now)
        changes = [
            r
            for r in state.date_changes_since(now - timedelta(days=days))
            if datetime.fromisoformat(r["start"]) >= now
        ]
        exams = upcoming_exams(state, now)
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
        console.print(f"  [yellow]●[/yellow] {r['text']}")
    if not changes and not first:
        console.print("  [dim]none[/dim]")
    console.print("\n[bold]Exams[/bold]")
    for d in exams:
        until = (d.start.date() - now.date()).days
        place = f" · {d.location}" if d.location else ""
        cancelled = " [red]cancelled[/red]" if d.cancelled else ""
        console.print(f"  {d.when()}  {d.course}{place}  [dim]in {until} days[/dim]{cancelled}")
    if not exams:
        console.print("  [dim]none in Stud.IP yet (dates with the category Klausur/Prüfung)[/dim]")


# -- PDF passwords ----------------------------------------------------------------------


@pdf_app.command("set")
def pdf_password_set(
    course: Annotated[str, typer.Argument(help="Course ID, title or target (substring).")],
    config: ConfigOpt = None,
) -> None:
    """Store the course's PDF password and unlock the PDFs synced so far.

    From then on every new PDF of the course that is locked with this password
    is stored without it. PDFs that need another password stay as they are.
    """
    cfg = load_config(config)
    course_cfg = find_course(cfg, course)
    password = getpass.getpass(f"PDF password for {course_cfg.name or course_cfg.id}: ")
    if not password:
        raise fail("no password entered - nothing was stored")
    auth.set_pdf_password(course_cfg.id, password)
    try:
        with exclusive_lock(paths.lock_file()), State(paths.state_db()) as state:
            outcome = unlock_course(state, course_cfg.id, password)
    except Locked as exc:
        raise fail(f"{exc}; the PDFs are unlocked with the next sync instead") from None
    report.print_unlocked(console, outcome)
    console.print("[green]✓[/green] Password stored in the keychain; new PDFs are unlocked.")


@pdf_app.command("remove")
def pdf_password_remove(
    course: Annotated[str, typer.Argument(help="Course ID, title or target (substring).")],
    config: ConfigOpt = None,
) -> None:
    """Forget the course's PDF password (files already unlocked stay unlocked)."""
    course_cfg = find_course(load_config(config), course)
    removed = auth.delete_pdf_password(course_cfg.id)
    console.print("PDF password removed." if removed else "No PDF password was stored.")


@pdf_app.command("list")
def pdf_password_list(config: ConfigOpt = None) -> None:
    """Courses with a stored PDF password."""
    cfg = load_config(config)
    names = [c.name or c.id for c in cfg.courses.values() if auth.get_pdf_password(c.id)]
    if OPTS.json:
        emit_json(names)
        return
    console.print("\n".join(names) if names else "No course has a PDF password.")


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


@app.command("tui")
def tui_cmd() -> None:
    """Open the interactive terminal UI (same as running `studip` without a command)."""
    from .tui import run_tui

    run_tui()


def main() -> None:
    app()
