"""CLI commands grouped by user workflow."""

from __future__ import annotations

import getpass
import tomllib
from typing import Annotated

import typer
from rich.prompt import Confirm

from . import auth, paths, report
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
from .config import ConfigError
from .executor import unlock_course
from .fsutil import Locked, exclusive_lock
from .report import esc
from .state import State


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
    try:
        auth.set_password(cfg.base_url, cfg.username, password)
    except auth.AuthError as exc:
        raise fail(f"{exc} - nothing was stored", 3) from None
    paths.auth_pause_file().unlink(missing_ok=True)  # resume auto-sync
    console.print(
        f"[green]✓[/green] Logged in as {esc(me['name'] or cfg.username)}; "
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
    try:
        removed = auth.delete_password(cfg.base_url, cfg.username)
    except auth.AuthError as exc:
        raise fail(str(exc), 3) from None
    console.print("Password removed." if removed else "No password was stored.")


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
        f"[green]✓[/green] {esc(cfg.path)}: {esc(active)} active, "
        f"{len(cfg.courses) - active} excluded courses, {rules} rules"
    )


@config_app.command("set")
def config_set(
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. sync.on_update")],
    value: Annotated[str, typer.Argument(help='TOML value: skip, true, 200, "text"')],
) -> None:
    """Change one setting in place (comments are kept; the result is validated)."""

    from .config_edit import set_value

    try:
        parsed = tomllib.loads(f"v = {value}")["v"]
    except tomllib.TOMLDecodeError:
        parsed = value  # bare words are strings: `studip config set sync.on_update skip`
    try:
        set_value(paths.config_file(), tuple(key.split(".")), parsed)
    except ConfigError as exc:
        raise fail(f"not changed: {'; '.join(exc.problems)}", 2) from None
    console.print(f"[green]✓[/green] {esc(key)} = {esc(repr(parsed))}")


@config_app.command("remove-course")
def config_remove_course(
    course: Annotated[str, typer.Argument(help="Course id, folder or name from the config.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False,
) -> None:
    """Remove a course and its rules from the config (e.g. one you left on Stud.IP).

    Files already synced stay where they are.
    """
    from .config_edit import remove_course

    cfg = load_config(None)
    wanted = course.casefold()
    found = [
        c
        for c in cfg.courses.values()
        if wanted in (c.id.casefold(), (c.target or "").casefold(), (c.name or "").casefold())
    ]
    if len(found) != 1:
        problem = "not in the config" if not found else "matches several courses; use the id"
        raise fail(f"{course!r} {problem} (see `studip courses`)", 2)
    target = found[0]
    label = target.name or target.target or target.id
    rules = len(target.rules)
    if not yes and not Confirm.ask(
        f"Remove {esc(label)} from the config"
        + (f" with its {rules} rule(s)" if rules else "")
        + "? Files already synced stay",
        default=False,
    ):
        raise typer.Exit(1)
    try:
        remove_course(cfg.path, target.id)
    except (ConfigError, OSError) as exc:
        raise fail(f"not removed: {exc}") from None
    if OPTS.json:
        emit_json({"removed": target.id})
        return
    console.print(f"[green]✓[/green] removed {esc(label)} (backup: {esc(cfg.path)}.bak)")


@config_app.command("edit")
def config_edit() -> None:
    """Open the config in $EDITOR and validate it afterwards."""
    path = paths.config_file()
    if not path.exists():
        raise fail(f"{path} does not exist; run `studip init` first")
    from .config_edit import open_in_editor

    if problem := open_in_editor(path):
        raise fail(problem)
    config_validate(None)


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
    try:
        auth.set_pdf_password(course_cfg.id, password, base_url=cfg.base_url, username=cfg.username)
    except auth.AuthError as exc:
        raise fail(f"{exc} - nothing was stored", 3) from None
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
    cfg = load_config(config)
    course_cfg = find_course(cfg, course)
    try:
        removed = auth.delete_pdf_password(
            course_cfg.id, base_url=cfg.base_url, username=cfg.username
        )
    except auth.AuthError as exc:
        raise fail(str(exc), 3) from None
    console.print("PDF password removed." if removed else "No PDF password was stored.")


@pdf_app.command("list")
def pdf_password_list(config: ConfigOpt = None) -> None:
    """Courses with a stored PDF password."""
    cfg = load_config(config)
    names = [
        c.name or c.id
        for c in cfg.courses.values()
        if auth.get_pdf_password(c.id, base_url=cfg.base_url, username=cfg.username)
    ]
    if OPTS.json:
        emit_json(names)
        return
    console.print("\n".join(names) if names else "No course has a PDF password.")
