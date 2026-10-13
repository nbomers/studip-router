"""The Textual app and its home screen."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import ClassVar

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from .. import __version__, auth, paths
from ..api import ApiError, AuthFailed, StudipClient
from ..config import Config, ConfigError, load
from ..news import fetch_news, unread
from ..state import State
from ..sync import CourseStatus, list_courses, resolve_semester
from .settings import THEMES, SettingsScreen
from .setup import SetupScreen
from .views import (
    CoursesView,
    DoctorView,
    HistoryView,
    MergeView,
    NewsView,
    ReorganizeView,
    RulesView,
    SyncView,
)
from .widgets import HelpScreen, PasswordScreen

LOGO = "[b]studip[/b][dim]·[/dim]router"

# key, title, description, factory
ACTIONS: list[tuple[str, str, str, Callable[[], Screen]]] = [
    ("s", "Sync now", "download what is new, after a short summary", lambda: SyncView(True)),
    ("p", "Preview", "every file and its target, nothing changes", SyncView),
    ("w", "What's new", "recently synced files: open, reveal, preview", HistoryView),
    ("a", "Announcements", "news from your courses", NewsView),
    ("c", "Courses", "turn syncing on/off, give new courses a folder", CoursesView),
    ("r", "Rules", "suggestions, test a file name, your rules", RulesView),
    ("o", "Reorganize", "apply changed rules to synced files (undo: u)", ReorganizeView),
    ("m", "Merge slides", "combine lecture PDFs into one file", MergeView),
    ("comma", "Settings", "sync, file names, auto-sync, theme, account", SettingsScreen),
    ("d", "Doctor", "check config, folders, login and server", DoctorView),
]
NEEDS_LOGIN = {"s", "p", "a", "c", "r"}
KEY_LABEL = {"comma": ","}


def _summary(summary: dict) -> str:
    parts = []
    for key, label in (("new", "new"), ("failed", "failed"), ("course_errors", "course errors")):
        if summary.get(key):
            parts.append(f"{summary[key]} {label}")
    if summary.get("interrupted"):
        parts.append("interrupted")
    return " · ".join(parts) or ("nothing new" if summary else "")


def _ago(iso: str | None) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    minutes = int((datetime.now(UTC) - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return then.astimezone().strftime("%d.%m.%Y")


class HomeScreen(Screen):
    BINDINGS: ClassVar = [
        *[Binding(key, f"run('{key}')", title, show=False) for key, title, _, _ in ACTIONS],
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("e", "edit_config", "Edit config", show=False),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(LOGO, id="logo")
            yield Static(id="who")
        with Horizontal(id="cards"):
            yield Static(id="card-sync", classes="card")
            yield Static(id="card-files", classes="card")
            yield Static(id="card-news", classes="card")
            yield Static(id="card-todo", classes="card")
        yield OptionList(*[self._option(*a[:3]) for a in ACTIONS], id="actions")
        yield Footer()

    @staticmethod
    def _option(key: str, title: str, description: str) -> Option:
        text = Text(no_wrap=True, overflow="ellipsis")  # narrow terminals: cut, never wrap
        text.append(f" {KEY_LABEL.get(key, key):>2}  ", style="bold")
        text.append(f"{title:<15} ")
        text.append(description, style="dim")
        return Option(text, id=key)

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.refresh_status()

    def on_screen_resume(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        app: StudipRouterApp = self.app  # type: ignore[assignment]
        cfg = app.cfg
        titles = {"sync": "last sync", "files": "files", "news": "announcements", "todo": "to do"}
        cards = {k: self.query_one(f"#card-{k}", Static) for k in titles}
        for name, card in cards.items():
            card.border_title = titles[name]
        if cfg is None:
            self.query_one("#who", Static).update(f"[red]config problem[/red]  v{__version__}")
            cards["sync"].update("[red]config invalid[/red]")
            cards["todo"].update(
                f"[red]{app.config_error[:200]}[/red]\n[dim]e: open the config file[/dim]"
            )
            return
        logged_in = auth.has_password(cfg.base_url, cfg.username)
        profile = f" · profile {paths.profile()}" if paths.profile() else ""
        self.query_one("#who", Static).update(
            f"{cfg.username} @ {cfg.base_url.split('//')[-1]}{profile}  [dim]v{__version__}[/dim]"
        )
        with State(paths.state_db()) as state:
            last = state.last_run()
            tracked = len(state.all())
            default_routed = len(state.default_routed())
        last_summary: dict = {}
        if last and last["summary"]:
            with contextlib.suppress(ValueError):
                last_summary = json.loads(last["summary"])
        when = _ago(last["finished_at"] if last else None)
        cards["sync"].update(f"[b]{when}[/b]\n[dim]{_summary(last_summary)}[/dim]")
        active = sum(1 for c in cfg.courses.values() if not c.exclude)
        courses = f"{active} course" + ("s" if active != 1 else "")
        cards["files"].update(f"[b]{tracked}[/b] synced\n[dim]{courses}[/dim]")
        self._local = {
            "default_routed": default_routed,
            "failed": int(last_summary.get("failed", 0) or 0),
            "course_errors": int(last_summary.get("course_errors", 0) or 0),
            "paused": paths.auth_pause_file().exists(),
            "logged_in": logged_in,
        }
        cached = app.status_cache()
        self._show_status(cached)
        if logged_in and cached is None:
            self.load_remote_status()

    def _show_status(self, remote: dict | None) -> None:
        local = self._local
        news = self.query_one("#card-news", Static)
        todo: list[str] = []
        if local["paused"]:
            todo.append("[red]auto-sync paused[/red] [dim](log in: , then L)[/dim]")
        if not local["logged_in"]:
            news.update("[dim]log in to see[/dim]")
            todo.append("[yellow]not logged in[/yellow] [dim](s asks for it)[/dim]")
        elif remote is None:
            news.update("[dim]…[/dim]")
        else:
            unread_count = remote["unread"]
            news.update(
                f"[b]{unread_count}[/b] unread" + (" [dim](a)[/dim]" if unread_count else "")
            )
            if remote["new_courses"]:
                todo.append(
                    f"[yellow]{remote['new_courses']} new course(s)[/yellow] [dim](c)[/dim]"
                )
        if local["course_errors"]:
            todo.append(f"[red]{local['course_errors']} course(s) failed[/red] [dim](p)[/dim]")
        if local["failed"]:
            todo.append(f"[red]{local['failed']} file(s) failed[/red] [dim](p)[/dim]")
        if local["default_routed"]:
            todo.append(f"{local['default_routed']} in default folder [dim](r)[/dim]")
        self.query_one("#card-todo", Static).update(
            "\n".join(todo[:3]) or "[green]✓ all good[/green]"
        )

    def load_remote_status(self) -> None:
        # A daemon thread instead of a worker: quitting must never wait for a slow
        # or unreachable server just to fill the status cards.
        threading.Thread(target=self._remote_status, daemon=True).start()

    def _remote_status(self) -> None:
        app: StudipRouterApp = self.app  # type: ignore[assignment]
        cfg = app.cfg
        if cfg is None:
            return
        try:
            with app.make_client() as client, State(paths.state_db()) as state:
                sem = resolve_semester(client, cfg.semester)
                entries, _ = list_courses(client, cfg, sem)
                items = [
                    n
                    for c in cfg.courses.values()
                    if not c.exclude
                    for n in fetch_news(client, c.id, c.name or c.target)
                ]
                fresh = unread(state, items)
        except Exception:  # status cards are best effort, never crash the app
            return
        remote = {
            "unread": len(fresh),
            "new_courses": sum(1 for e in entries if e.status is CourseStatus.UNCONFIGURED),
        }
        app.set_status_cache(remote)
        with contextlib.suppress(RuntimeError):  # app already closed
            app.call_from_thread(self._show_status, remote)

    def action_move(self, step: int) -> None:
        actions = self.query_one(OptionList)
        if step > 0:
            actions.action_cursor_down()
        else:
            actions.action_cursor_up()

    @on(OptionList.OptionSelected, "#actions")
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.action_run(event.option.id or "")

    def action_run(self, key: str) -> None:
        self.app.open_action(key)  # type: ignore[attr-defined]

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_edit_config(self) -> None:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        with self.app.suspend():
            subprocess.run([*editor.split(), str(paths.config_file())], check=False)
        self.app.reload_config()  # type: ignore[attr-defined]
        self.refresh_status()


class StudipRouterApp(App):
    TITLE = "studip-router"
    CSS = """
    Screen { background: $background; }
    #top { height: 3; padding: 1 2 0 2; }
    #logo { width: 1fr; color: $accent; }
    #who { width: auto; color: $text-muted; }
    #cards { height: 5; padding: 0 1; }
    .card { width: 1fr; height: 5; border: round $panel-lighten-2; padding: 0 1; margin: 0 1;
            border-title-color: $text-muted; }
    #actions { height: 1fr; margin: 1 2; border: round $accent 50%; padding: 0 1; }
    #actions:focus { border: round $accent; }
    .view-title { background: $accent 20%; color: $text; padding: 0 1; height: 1; }
    .status { padding: 1 2; height: auto; }
    .section { padding: 1 0 0 0; height: auto; }
    .section-title { color: $accent; text-style: bold; padding: 1 2 0 2; height: auto; }
    DataTable { height: 1fr; }
    #progress { padding: 0 2; height: 1; width: 100%; }
    #progress Bar { width: 1fr; }
    #log, #merge-log, #doctor-log { height: 1fr; border: round $panel; padding: 0 1; }
    #news-list { width: 45%; }
    #news-body { padding: 1 2; border: round $panel; }
    .form { padding: 1 2; height: auto; }
    .form Label { margin-top: 1; }
    .form Input { width: 80; }
    ConfirmScreen, PasswordScreen, ChoiceScreen, HelpScreen { align: center middle; }
    .dialog { width: 72; height: auto; max-height: 90%; padding: 1 2; border: thick $accent;
              background: $surface; }
    .dialog OptionList { height: auto; max-height: 20; }
    .help { width: 76; }
    .question { text-style: bold; margin-bottom: 1; }
    .detail { color: $text-muted; margin-bottom: 1; }
    .buttons { height: auto; align-horizontal: right; margin-top: 1; }
    .buttons Button { margin-left: 2; }
    .dim { color: $text-muted; }
    .error { color: $error; }
    #course-select { height: auto; max-height: 20; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.cfg: Config | None = None
        self.config_error = ""
        self._status: tuple[float, dict] | None = None  # (time, remote status)

    STATUS_TTL = 300  # seconds; the home screen must not hammer Stud.IP

    def status_cache(self) -> dict | None:
        if self._status and time.monotonic() - self._status[0] < self.STATUS_TTL:
            return self._status[1]
        return None

    def set_status_cache(self, remote: dict) -> None:
        self._status = (time.monotonic(), remote)

    def invalidate_status(self) -> None:
        self._status = None

    def on_mount(self) -> None:
        self.reload_config()
        if self.cfg is None and not paths.config_file().exists():
            self.push_screen(SetupScreen())
            return
        self.push_screen(HomeScreen())

    def reload_config(self) -> None:
        try:
            self.cfg = load(paths.config_file())
            self.config_error = ""
            if self.cfg.ui_theme in THEMES or self.cfg.ui_theme in self.available_themes:
                self.theme = self.cfg.ui_theme
        except ConfigError as exc:
            self.cfg = None
            self.config_error = "; ".join(exc.problems)

    def finish_setup(self) -> None:
        self.reload_config()
        self.switch_screen(HomeScreen())
        self.notify("Setup done. Press s to sync, r for rule suggestions.")

    def refresh_home(self, remote_changed: bool = False) -> None:
        if remote_changed:
            self.invalidate_status()
        for screen in self.screen_stack:
            if isinstance(screen, HomeScreen):
                screen.refresh_status()

    def make_client(self) -> StudipClient:
        assert self.cfg is not None
        password = auth.get_password(self.cfg.base_url, self.cfg.username)
        return StudipClient(self.cfg.base_url, self.cfg.username, password)

    def open_action(self, key: str) -> None:
        factory = next((f for k, _, _, f in ACTIONS if k == key), None)
        if factory is None:
            return
        if self.cfg is None:
            self.notify("The config has a problem - press e to open it.", severity="error")
            return
        if key in NEEDS_LOGIN and not auth.has_password(self.cfg.base_url, self.cfg.username):
            self.ask_password(lambda: self.push_screen(factory()))
            return
        self.push_screen(factory())

    def ask_password(self, then: Callable[[], object], error: str = "") -> None:
        assert self.cfg is not None
        cfg = self.cfg

        def got(password: str | None) -> None:
            if not password:
                return
            try:
                with StudipClient(cfg.base_url, cfg.username, password) as client:
                    client.me()
            except AuthFailed:
                self.ask_password(then, "Wrong username or password.")
                return
            except ApiError as exc:
                self.notify(str(exc), severity="error")
                return
            auth.set_password(cfg.base_url, cfg.username, password)
            paths.auth_pause_file().unlink(missing_ok=True)  # resume auto-sync
            self.refresh_home(remote_changed=True)
            then()

        self.push_screen(PasswordScreen(cfg, error), got)


def run_tui() -> None:
    StudipRouterApp().run()
