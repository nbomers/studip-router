"""The Textual app and its home screen."""

from __future__ import annotations

import contextlib
import json
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
from ..config_edit import open_in_editor
from ..news import fetch_news, unread
from ..reorganize import pending_moves
from ..report import esc
from ..state import State
from ..sync import CourseStatus, list_courses, resolve_semester
from .settings import THEMES, SettingsScreen
from .setup import SetupScreen
from .views import (
    CoursesView,
    DatesView,
    DoctorView,
    HistoryView,
    MergeView,
    NewsView,
    ReorganizeView,
    RulesView,
    SyncView,
)
from .widgets import (
    HelpScreen,
    PasswordScreen,
    load_course_groups,
    save_course_groups,
    set_course_order,
)

LOGO = "[b]studip[/b][dim]·[/dim]router"

# key, title, description, factory. The first PRIMARY ones are always on the home
# screen; the rest sit behind "More" (their keys work everywhere anyway).
ACTIONS: list[tuple[str, str, str, Callable[[], Screen]]] = [
    ("s", "Sync now", "get new files", lambda: SyncView(True)),
    ("w", "What's new", "latest files", HistoryView),
    ("a", "Announcements", "course news", NewsView),
    ("t", "Dates", "changes, exams", DatesView),
    ("c", "Courses", "what to sync", CoursesView),
    ("p", "Preview", "where files would go", SyncView),
    ("r", "Rules", "sort files your way", RulesView),
    ("o", "Reorganize", "move to new folders", ReorganizeView),
    ("m", "Merge slides", "all slides in one PDF", MergeView),
    ("comma", "Settings", "options, account", SettingsScreen),
    ("d", "Doctor", "check the setup", DoctorView),
]
PRIMARY = {"s", "w", "a", "t", "c"}
NEEDS_LOGIN = {"s", "p", "a", "c", "r", "t"}
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
        return f"{esc(minutes)} min ago"
    if minutes < 48 * 60:
        return f"{esc(minutes // 60)} h ago"
    return then.astimezone().strftime("%d.%m.%Y")


class HomeScreen(Screen):
    """One suggested next step, five everyday actions, the rest behind "More"."""

    BINDINGS: ClassVar = [
        *[
            Binding(key, f"run('{key}')", title, show=key == "comma")
            for key, title, _, _ in ACTIONS
        ],
        Binding("plus", "toggle_more", "More"),
        Binding("j", "move(1)", show=False),
        Binding("k", "move(-1)", show=False),
        Binding("question_mark", "help", "Help"),
        Binding("e", "edit_config", "Edit config", show=False),
        Binding("q", "app.quit", "Quit"),
    ]
    more = False  # "More" expanded; kept while the app runs

    def compose(self) -> ComposeResult:
        with Horizontal(id="top"):
            yield Static(LOGO, id="logo")
            yield Static(id="who")
        yield Static(id="next", classes="card")
        yield Static(id="facts")
        yield OptionList(id="actions")
        yield Footer()

    @staticmethod
    def _option(key: str, title: str, description: str, dim: bool = False) -> Option:
        text = Text(no_wrap=True, overflow="ellipsis")  # narrow terminals: cut, never wrap
        text.append(f" {KEY_LABEL.get(key, key):>2}  ", style="bold dim" if dim else "bold")
        text.append(f"{title:<15} ", style="dim" if dim else "")
        text.append(description, style="dim")
        return Option(text, id=key)

    def _fill_actions(self, next_key: str = "", next_label: str = "") -> None:
        self._next = (next_key, next_label)
        actions = self.query_one("#actions", OptionList)
        highlighted = actions.highlighted
        actions.clear_options()
        if next_key:
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append(" ↵  ", style="bold")
            text.append(next_label, style="bold")
            actions.add_option(Option(text, id=f"next:{next_key}"))
            actions.add_option(None)
        actions.add_options([self._option(*a[:3]) for a in ACTIONS if a[0] in PRIMARY])
        if HomeScreen.more:
            actions.add_option(None)
            actions.add_options(
                [self._option(*a[:3], dim=True) for a in ACTIONS if a[0] not in PRIMARY]
            )
            actions.add_option(Option(Text("  +  Fewer", style="dim"), id="more"))
        else:
            hidden = "preview, rules, merge, settings …"
            text = Text(no_wrap=True, overflow="ellipsis")
            text.append("  +  ", style="bold")
            text.append(f"{'More':<15} ")
            text.append(hidden, style="dim")
            actions.add_option(Option(text, id="more"))
        if highlighted is not None and highlighted < actions.option_count:
            actions.highlighted = highlighted
        else:
            actions.highlighted = 0

    def on_mount(self) -> None:
        self._fill_actions()
        self.query_one(OptionList).focus()
        self.refresh_status()

    def on_screen_resume(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        app: StudipRouterApp = self.app  # type: ignore[assignment]
        cfg = app.cfg
        self.query_one("#next", Static).border_title = "next step"
        if cfg is None:
            self.query_one("#who", Static).update(f"[red]config problem[/red]  v{__version__}")
            self.query_one("#next", Static).update(
                f"[red]The config file has a problem.[/red]\n[dim]{esc(app.config_error[:200])}"
                "[/dim]"
            )
            self.query_one("#facts", Static).update("")
            self._fill_actions("e", "Open the config file")
            return
        logged_in = auth.has_password(cfg.base_url, cfg.username)
        profile = f" · profile {esc(paths.profile())}" if paths.profile() else ""
        self.query_one("#who", Static).update(
            f"{esc(cfg.username)} @ {esc(cfg.base_url.split('//')[-1])}{profile}"
            f"  [dim]v{__version__}[/dim]"
        )
        with State(paths.state_db()) as state:
            last = state.last_run()
            tracked = len(state.all())
            default_routed = len(state.default_routed())
            to_move = len(pending_moves(cfg, state))
        last_summary: dict = {}
        if last and last["summary"]:
            with contextlib.suppress(ValueError):
                last_summary = json.loads(last["summary"])
        active = sum(1 for c in cfg.courses.values() if not c.exclude)
        self._local = {
            "never": last is None,
            "when": _ago(last["finished_at"] if last else None),
            "last": _summary(last_summary),
            "tracked": tracked,
            "courses": active,
            "default_routed": default_routed,
            "default_folder": cfg.default_target,
            "to_move": to_move,
            "failed": int(last_summary.get("failed", 0) or 0),
            "course_errors": int(last_summary.get("course_errors", 0) or 0),
            "paused": paths.auth_pause_file().exists(),
            "logged_in": logged_in,
        }
        cached = app.status_cache()
        self._show_status(cached)
        if logged_in and cached is None:
            self.load_remote_status()

    def _steps(self, remote: dict | None) -> list[tuple[str, str, str]]:
        """What needs doing, most important first: (message, key, action label)."""
        local = self._local
        steps: list[tuple[str, str, str]] = []
        if local["paused"]:
            steps.append(
                (
                    "[red]Auto-sync is paused: Stud.IP rejected the password.[/red]",
                    "login",
                    "Log in again",
                )
            )
        elif not local["logged_in"]:
            steps.append(("Log in to Stud.IP to get started.", "login", "Log in"))
        if local["course_errors"]:
            steps.append(
                (
                    f"[red]{local['course_errors']} course(s) could not be read last time.[/red]",
                    "p",
                    "See what is wrong",
                )
            )
        if local["failed"]:
            steps.append(
                (f"[red][b]{local['failed']}[/b] file(s) failed last time.[/red]", "s", "Try again")
            )
        if remote and remote["new_courses"]:
            n = remote["new_courses"]
            steps.append(
                (
                    f"[yellow]{n} new course{'s' if n != 1 else ''} on Stud.IP.[/yellow]",
                    "c",
                    "Choose whether to sync " + ("them" if n != 1 else "it"),
                )
            )
        if local["never"] and local["logged_in"]:
            steps.append(("[b]Ready[/b] for the first sync.", "s", "Start the first sync"))
        if local["to_move"]:
            steps.append(
                (
                    f"[yellow]{local['to_move']} synced file(s) belong in another folder now."
                    "[/yellow]",
                    "o",
                    "Move them",
                )
            )
        if local["default_routed"]:
            steps.append(
                (
                    f"[yellow]{local['default_routed']} file(s) landed in "
                    f"{esc(local['default_folder'])}.[/yellow]",
                    "r",
                    "Sort them with rules",
                )
            )
        if remote and remote["unread"]:
            steps.append((f"[b]{remote['unread']}[/b] unread announcement(s).", "a", "Read them"))
        return steps

    def _show_status(self, remote: dict | None) -> None:
        local = self._local
        steps = self._steps(remote)
        if steps:
            message, key, label = steps[0]
            rest = [m for m, _, _ in steps[1:3]]
        else:
            message, key, label = (
                f"[green]✓ [b]All good.[/b][/green] Last sync [b]{esc(local['when'])}[/b].",
                "s",
                "Sync now",
            )
            rest = []
        body = message  # the action itself is the highlighted line below
        if rest:
            body += "\n[dim]then: " + " · ".join(rest) + "[/dim]"
        self.query_one("#next", Static).update(body)
        self._fill_actions(key, label)

        facts = [f"last sync [b]{esc(local['when'])}[/b]"]
        if local["last"]:
            facts[0] += f" [dim]({esc(local['last'])})[/dim]"
        facts.append(
            f"[b]{local['tracked']}[/b] files in [b]{local['courses']}[/b] "
            f"course{'s' if local['courses'] != 1 else ''}"
        )
        if remote is not None:
            facts.append(f"[b]{esc(remote['unread'])}[/b] unread")
        self.query_one("#facts", Static).update("  ·  ".join(facts))

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
        try:  # your Stud.IP colours, for the next screens drawn
            with app.make_client() as client:
                save_course_groups(client.course_groups(client.me()["id"]))
        except Exception:
            pass
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
        option_id = event.option.id or ""
        if option_id == "more":
            self.action_toggle_more()
            return
        self.action_run(option_id.removeprefix("next:"))

    def action_toggle_more(self) -> None:
        HomeScreen.more = not HomeScreen.more
        self._fill_actions(*self._next)

    def action_run(self, key: str) -> None:
        if key == "login":
            self.app.ask_password(self.refresh_status)  # type: ignore[attr-defined]
        elif key == "e":
            self.action_edit_config()
        else:
            self.app.open_action(key)  # type: ignore[attr-defined]

    def action_help(self) -> None:
        self.app.push_screen(HelpScreen())

    def action_edit_config(self) -> None:
        with self.app.suspend():
            problem = open_in_editor(paths.config_file())
        if problem:
            self.notify(esc(problem), severity="error")
        self.app.reload_config()  # type: ignore[attr-defined]
        self.refresh_status()


class StudipRouterApp(App):
    """The terminal UI.

    Crash tracebacks are printed without local variables: they could contain the
    Stud.IP or a PDF password (Textual shows locals by default).
    """

    TITLE = "studip-router"
    CSS = """
    Screen { background: $background; }
    #top { height: 3; padding: 1 2 0 2; }
    #logo { width: 1fr; color: $accent; }
    #who { width: auto; color: $text-muted; }
    #next { height: auto; min-height: 3; border: round $accent; padding: 0 1; margin: 1 2 0 2;
            border-title-color: $accent; border-title-style: bold; }
    #facts { height: 1; padding: 0 3; margin-top: 1; color: $text-muted; }
    #actions { height: 1fr; margin: 1 2; border: round $accent 50%; padding: 0 1; }
    #actions:focus { border: round $accent; }
    .view-title { background: $accent 20%; color: $text; padding: 0 1; height: 1; }
    .status { padding: 1 2; height: auto; }
    .section { padding: 1 0 0 0; height: auto; }
    .section-title { color: $accent; text-style: bold; padding: 1 2 0 2; height: auto; }
    DataTable { height: 1fr; }
    #progress, #busy { padding: 0 2; height: 1; width: 100%; }
    #progress Bar, #busy Bar { width: 1fr; }
    #log, #merge-log, #doctor-log, #dates-log { height: 1fr; border: round $panel; padding: 0 1; }
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
        from ..report import setup_file_logging

        with contextlib.suppress(OSError):  # "details in the log" holds in the UI too
            setup_file_logging(paths.log_file(), verbose=False)
        self.reload_config()
        if self.cfg is None and not paths.config_file().exists():
            self.push_screen(SetupScreen())
            return
        self.push_screen(HomeScreen())

    def reload_config(self) -> None:
        try:
            self.cfg = load(paths.config_file())
            self.config_error = ""
            set_course_order(list(self.cfg.courses))
            load_course_groups()
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

    def action_open_settings(self) -> None:
        """Settings from every screen (","); never stacked twice."""
        if isinstance(self.screen, SettingsScreen):
            return
        self.open_action("comma")

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
            try:
                auth.set_password(cfg.base_url, cfg.username, password)
            except auth.AuthError as exc:
                self.notify(str(exc), severity="error", timeout=20)
                return
            paths.auth_pause_file().unlink(missing_ok=True)  # resume auto-sync
            self.refresh_home(remote_changed=True)
            then()

        self.push_screen(PasswordScreen(cfg, error), got)

    def _fatal_error(self) -> None:
        """Like Textual's own handler, but never renders local variables."""
        import rich
        from rich.segment import Segments
        from rich.traceback import Traceback

        self.bell()
        traceback = Traceback(show_locals=False, width=None, suppress=[rich])
        self._exit_renderables.append(
            Segments(self.console.render(traceback, self.console.options))
        )
        self._close_messages_no_wait()


def run_tui() -> None:
    StudipRouterApp().run()
