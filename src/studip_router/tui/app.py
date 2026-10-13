"""The Textual app and its home screen."""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
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
    ("p", "Preview", "every file and where it would go - nothing is changed", SyncView),
    ("w", "What's new", "recently synced files: open, reveal, preview", HistoryView),
    ("a", "Announcements", "news from your courses", NewsView),
    ("c", "Courses", "turn syncing on/off, give new courses a folder", CoursesView),
    ("r", "Rules", "suggestions, test a file name, your rules", RulesView),
    ("o", "Reorganize", "apply changed rules to synced files (undo: u)", ReorganizeView),
    ("m", "Merge slides", "combine lecture PDFs into one file", MergeView),
    ("comma", "Settings", "behaviour, file names, auto-sync, theme, account", SettingsScreen),
    ("d", "Doctor", "check config, folders, login and server", DoctorView),
]
NEEDS_LOGIN = {"s", "p", "a", "c", "r"}
KEY_LABEL = {"comma": ","}


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
        text = Text()
        text.append(f" {KEY_LABEL.get(key, key):>2}  ", style="bold")
        text.append(f"{title:<16}")
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
        cards = {k: self.query_one(f"#card-{k}", Static) for k in ("sync", "files", "news", "todo")}
        for name, card in cards.items():
            card.border_title = {
                "sync": "last sync",
                "files": "files",
                "news": "announcements",
                "todo": "to do",
            }[name]
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
        summary = ""
        if last and last["summary"]:
            summary = f"\n[dim]{last['summary'][:60]}[/dim]"
        cards["sync"].update(f"[b]{_ago(last['finished_at'] if last else None)}[/b]{summary}")
        active = sum(1 for c in cfg.courses.values() if not c.exclude)
        courses = f"{active} course" + ("s" if active != 1 else "")
        cards["files"].update(f"[b]{tracked}[/b] synced\n[dim]{courses}[/dim]")
        if not logged_in:
            cards["news"].update("[dim]log in to see[/dim]")
            cards["todo"].update(
                "[yellow]not logged in[/yellow]\n[dim]press s - you will be "
                "asked for your password[/dim]"
            )
            return
        todo = []
        if default_routed:
            todo.append(f"{default_routed} file(s) in the default folder [dim](r)[/dim]")
        cards["todo"].update("\n".join(todo) or "[green]✓ all good[/green]")
        self.load_remote_status(default_routed)

    def load_remote_status(self, default_routed: int) -> None:
        # A daemon thread instead of a worker: quitting must never wait for a slow
        # or unreachable server just to fill the status cards.
        threading.Thread(target=self._remote_status, args=(default_routed,), daemon=True).start()

    def _remote_status(self, default_routed: int) -> None:
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
        new_courses = sum(1 for e in entries if e.status is CourseStatus.UNCONFIGURED)

        def show() -> None:
            news = self.query_one("#card-news", Static)
            news.update(f"[b]{len(fresh)}[/b] unread" + (" [dim](a)[/dim]" if fresh else ""))
            todo = []
            if new_courses:
                todo.append(f"[yellow]{new_courses} new course(s)[/yellow] [dim](c)[/dim]")
            if default_routed:
                todo.append(f"{default_routed} file(s) in the default folder [dim](r)[/dim]")
            self.query_one("#card-todo", Static).update(
                "\n".join(todo) or "[green]✓ all good[/green]"
            )

        with contextlib.suppress(RuntimeError):  # app already closed
            app.call_from_thread(show)

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
    #cards { height: 6; padding: 0 1; }
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

    def refresh_home(self) -> None:
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
            self.refresh_home()
            then()

        self.push_screen(PasswordScreen(cfg, error), got)


def run_tui() -> None:
    StudipRouterApp().run()
