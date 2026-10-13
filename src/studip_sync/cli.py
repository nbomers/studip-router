"""Command line interface (`studip`)."""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from . import auth, paths, report
from .api import ApiError, AuthFailed, StudipClient
from .config import Config, ConfigError, load
from .executor import Outcome, execute
from .planner import Kind
from .state import State
from .sync import build_plan, list_courses, resolve_semester

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Sync Stud.IP course files.")
auth_app = typer.Typer(no_args_is_help=True, help="Manage stored credentials.")
config_app = typer.Typer(no_args_is_help=True, help="Inspect the configuration.")
app.add_typer(auth_app, name="auth")
app.add_typer(config_app, name="config")

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

ConfigOpt = Annotated[
    Path | None, typer.Option("--config", "-c", help="Config file (default: platform location).")
]


def _load_config(path: Path | None) -> Config:
    try:
        return load(path or paths.config_file())
    except ConfigError as exc:
        err.print(f"[red]Invalid config[/red] {exc}")
        raise typer.Exit(2) from None


def _client(cfg: Config) -> StudipClient:
    try:
        password = auth.get_password(cfg.base_url, cfg.username)
    except auth.AuthError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(3) from None
    return StudipClient(cfg.base_url, cfg.username, password)


@auth_app.command("login")
def auth_login(config: ConfigOpt = None) -> None:
    """Prompt for the password, verify it against the API, store it in the keychain."""
    cfg = _load_config(config)
    password = getpass.getpass(f"Password for {cfg.username} at {cfg.base_url}: ")
    try:
        with StudipClient(cfg.base_url, cfg.username, password) as client:
            me = client.me()
    except AuthFailed as exc:
        err.print(f"[red]{exc}[/red] - nothing was stored.")
        raise typer.Exit(3) from None
    except ApiError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    auth.set_password(cfg.base_url, cfg.username, password)
    console.print(
        f"[green]Login OK[/green] ({me['name'] or cfg.username}); password stored in keychain."
    )


@auth_app.command("status")
def auth_status(config: ConfigOpt = None) -> None:
    """Show whether a password is stored and whether it works."""
    cfg = _load_config(config)
    if not auth.has_password(cfg.base_url, cfg.username):
        console.print("No password stored. Run [bold]studip auth login[/bold].")
        raise typer.Exit(3)
    try:
        with _client(cfg) as client:
            client.me()
    except ApiError as exc:
        err.print(f"[red]Stored password does not work:[/red] {exc}")
        raise typer.Exit(3) from None
    console.print("[green]Stored password works.[/green]")


@auth_app.command("logout")
def auth_logout(config: ConfigOpt = None) -> None:
    """Remove the stored password from the keychain."""
    cfg = _load_config(config)
    removed = auth.delete_password(cfg.base_url, cfg.username)
    console.print("Password removed." if removed else "No password was stored.")


@config_app.command("path")
def config_path() -> None:
    """Print the config file location."""
    console.print(str(paths.config_file()))


@config_app.command("validate")
def config_validate(config: ConfigOpt = None) -> None:
    """Validate the config file."""
    cfg = _load_config(config)
    active = sum(1 for c in cfg.courses.values() if not c.exclude)
    console.print(
        f"[green]OK[/green] {cfg.path} ({active} active, "
        f"{len(cfg.courses) - active} excluded courses)"
    )


@app.command("courses")
def courses_cmd(
    config: ConfigOpt = None,
    semester: Annotated[
        str | None,
        typer.Option("--semester", "-s", help='"current", "all" or e.g. "WiSe 2026/27".'),
    ] = None,
) -> None:
    """List Stud.IP courses with their sync status."""
    cfg = _load_config(config)
    try:
        with _client(cfg) as client:
            sem = resolve_semester(client, semester or cfg.semester)
            entries, missing = list_courses(client, cfg, sem)
    except ApiError as exc:
        err.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(report.courses_table(entries, sem.title if sem else "all semesters"))
    for c in missing:
        console.print(f"[yellow]Configured but not found on Stud.IP:[/yellow] {c.id} {c.name}")


@app.command("sync")
def sync_cmd(
    config: ConfigOpt = None,
    course: Annotated[
        str | None, typer.Option("--course", help="Only this course (ID, title or target).")
    ] = None,
    semester: Annotated[str | None, typer.Option("--semester", "-s")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", "-n", help="Show the plan only.")] = False,
    include_large: Annotated[
        bool, typer.Option("--include-large", help="Ignore the size limit for this run.")
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also list skipped files.")
    ] = False,
) -> None:
    """Download new and changed files."""
    cfg = _load_config(config)
    report.setup_file_logging(paths.log_file(), verbose)
    with _client(cfg) as client, State(paths.state_db()) as state:
        try:
            with console.status("Fetching course data..."):
                sync_plan = build_plan(
                    client,
                    cfg,
                    state,
                    semester_name=semester,
                    course_query=course,
                    include_large=include_large,
                )
        except ApiError as exc:
            err.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None

        for plan in sync_plan.plans:
            if dry_run or verbose:
                table = report.plan_table(plan, verbose)
                if table is not None:
                    console.print(table)

        outcome = None
        if not dry_run:
            run_id = state.start_run("sync")
            outcome = Outcome()
            try:
                for plan in sync_plan.plans:
                    todo = [a for a in plan.actions if a.kind not in (Kind.SKIP, Kind.ERROR)]
                    if not todo:
                        continue
                    console.print(f"[bold]{plan.course.name or plan.course.id}[/bold]")
                    result = execute(
                        plan,
                        state,
                        client.download,
                        run_id,
                        on_progress=lambda a: console.print(
                            f"  [{report.STYLE[a.kind]}]{a.kind.value}[/] {a.dest.name}"
                        ),
                    )
                    outcome.done += result.done
                    outcome.failed += result.failed
            except KeyboardInterrupt:
                err.print(
                    "\n[yellow]Interrupted.[/yellow] Finished files are recorded; "
                    "no partial files were left behind. Run sync again to continue."
                )
                state.finish_run(run_id, {"interrupted": True})
                raise typer.Exit(130) from None
            state.finish_run(run_id, dict(report.summarize(sync_plan, outcome)))

        report.print_summary(console, sync_plan, dry_run, outcome)
        if outcome and outcome.failed:
            raise typer.Exit(1)


def main() -> None:
    app()
