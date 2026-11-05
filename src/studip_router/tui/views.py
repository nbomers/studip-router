"""Full-screen views opened from the home screen."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    DataTable,
    Footer,
    Input,
    Label,
    OptionList,
    ProgressBar,
    RichLog,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from .. import doctor, paths
from ..api import ApiError, AuthFailed, StudipClient
from ..config import Config, ConfigError
from ..config_edit import set_course
from ..dates import Change, check_dates, upcoming_exams
from ..fsutil import Locked
from ..models import RemoteFile
from ..news import News, fetch_news, unread
from ..planner import Action, Kind, build_router
from ..rename import RenameError
from ..reorganize import (
    Move,
    apply_reorganize,
    apply_undo,
    pending_moves,
    plan_reorganize,
    plan_undo,
)
from ..report import STYLE, SYMBOL, esc, reorganize_hint, summarize, unlocked_lines
from ..state import State
from ..suggest import Suggestion, local_category_dirs, local_subdirs, suggest_rules
from ..sync import (
    CourseEntry,
    CourseStatus,
    MergeOutcome,
    SyncPlan,
    build_plan,
    execute_plan,
    list_courses,
    resolve_semester,
    run_merges,
)
from ..tomlwrite import append_blocks, rule_block
from ..wizard import (
    course_folder_name,
    create_course_folder,
    local_course_folders,
    propose_target,
)
from .widgets import ChoiceScreen, ConfirmScreen, course_color


def clip(text: str, width: int) -> str:
    """Cut long names for display: "Introduction to Quantum Coo…"."""
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def plural(n: int, word: str, many: str = "") -> str:
    """ "[b]3[/b] files" - the number bold, the noun pluralised."""
    return f"[b]{n}[/b] {word if n == 1 else (many or word + 's')}"


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


# File types that `open`/`xdg-open` would run rather than show.
RUNNABLE = frozenset(
    [
        ".app",
        ".command",
        ".tool",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".csh",
        ".py",
        ".pyw",
        ".rb",
        ".pl",
        ".jar",
        ".desktop",
        ".run",
        ".bin",
        ".appimage",
        ".exe",
        ".msi",
        ".bat",
        ".cmd",
        ".com",
        ".ps1",
        ".vbs",
        ".scpt",
        ".applescript",
        ".workflow",
        ".pkg",
        ".mpkg",
        ".terminal",
        ".webloc",
        ".inetloc",
        ".url",
    ]
)


class View(Screen):
    """Full-screen view: title bar, Esc/q back, j/k move."""

    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("comma", "app.open_settings", "Settings"),
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
    ]
    TITLE = ""
    HINT = ""

    def compose_header(self) -> ComposeResult:
        hint = f"  [dim]{esc(self.HINT)}[/dim]" if self.HINT else ""
        yield Static(f" [b]{esc(self.TITLE)}[/b]{hint}", classes="view-title")
        busy = ProgressBar(id="busy", show_eta=False, show_percentage=False)
        busy.display = False
        yield busy

    # Progress while waiting for Stud.IP. Safe to call from worker threads.
    def busy(self, done: int | None = None, total: int | None = None, text: str = "") -> None:
        """Show the bar: with a total it fills up, without one it pulses."""
        self._call(self._busy, done, total, text)

    def idle(self) -> None:
        self._call(self._idle)

    def _call(self, fn, *args) -> None:
        import threading

        if threading.current_thread() is threading.main_thread():
            fn(*args)
        else:
            self.app.call_from_thread(fn, *args)

    def _busy(self, done: int | None, total: int | None, text: str) -> None:
        bar = self.query_one("#busy", ProgressBar)
        bar.display = True
        bar.update(total=total, progress=done or 0)
        if text:
            self.status(text)

    def _idle(self) -> None:
        self.query_one("#busy", ProgressBar).display = False

    def counted(self, items: list, label=lambda item: str(item)):
        """Iterate ``items`` and move the bar: "Reading 3/8: Quantum Cooking…"."""
        for index, item in enumerate(items):
            self.busy(
                index,
                len(items),
                f"Reading [b]{index + 1}/{len(items)}[/b]: {esc(clip(label(item), 50))}…",
            )
            yield item
        self.idle()

    @property
    def cfg(self) -> Config:
        return self.app.cfg  # type: ignore[attr-defined]

    def client(self) -> StudipClient:
        return self.app.make_client()  # type: ignore[attr-defined]

    def check_login(self, exc: ApiError) -> None:
        """From a worker: a rejected login pauses auto-sync and asks for the password,
        so neither the UI nor the background job keeps trying a wrong one."""
        if isinstance(exc, AuthFailed):
            self.app.call_from_thread(self._login_rejected)

    def _login_rejected(self) -> None:
        pause = paths.auth_pause_file()
        pause.parent.mkdir(parents=True, exist_ok=True)
        pause.write_text("login failed\n")
        self.app.ask_password(  # type: ignore[attr-defined]
            self.after_login, "Stud.IP rejected the stored password; auto-sync is paused."
        )

    def after_login(self) -> None:
        """Runs after a successful login following a rejected one."""

    def action_cursor(self, direction: str) -> None:
        focused = self.focused
        method = getattr(focused, f"action_cursor_{direction}", None)
        if method:
            method()

    def status(self, text: str, widget_id: str = "status") -> None:
        found = self.query(f"#{widget_id}")
        if found:  # views without a status line show progress in the bar only
            found.first(Static).update(text)


# -- sync -------------------------------------------------------------------------------


def plan_summary(plan: SyncPlan) -> str:
    """Confirm dialog text (markup): "[b]3 new[/b] · [b]1[/b] to the default folder"."""
    counts = summarize(plan)
    parts = []
    for key, label in (
        ("download", "new"),
        ("new-version", "new version(s)"),
        ("overwrite", "updated"),
        ("adopt", "already on disk"),
        ("mkdir", "folder(s) to create"),
        ("error", "problem(s)"),
    ):
        if counts.get(key):
            parts.append(f"[b]{counts[key]} {label}[/b]")
    default = counts.get("default-routed", 0)
    if default:
        parts.append(f"[yellow][b]{default}[/b] to the [b]default folder[/b][/yellow]")
    skipped = sum(skipped_reasons(plan).values())
    if skipped:
        parts.append(f"[dim]{skipped} not downloaded[/dim]")
    return " · ".join(parts) or "nothing to do"


def plan_summary_short(plan: SyncPlan) -> str:
    """Headline of the preview (markup): "[b]3 new[/b] files · [b]1 updated[/b]"."""
    counts = summarize(plan)
    new = counts.get("download", 0)
    updated = counts.get("new-version", 0) + counts.get("overwrite", 0)
    parts = []
    if new:
        parts.append(f"[b]{new} new[/b] file" + ("s" if new != 1 else ""))
    if updated:
        parts.append(f"[b]{updated} updated[/b]")
    other = sum(counts.get(k, 0) for k in ("adopt", "refresh", "rename", "mkdir"))
    if other and not parts:
        parts.append(f"[b]{other}[/b] small change(s)")
    return " · ".join(parts) or "nothing to download"


def skipped_reasons(plan: SyncPlan) -> dict[str, int]:
    """Why files are not downloaded, most frequent first: {"too large": 3, ...}."""
    reasons: dict[str, int] = {}
    for p in plan.plans:
        for a in p.actions:
            if a.kind is Kind.SKIP:
                reasons[a.reason or "skipped"] = reasons.get(a.reason or "skipped", 0) + 1
    return dict(sorted(reasons.items(), key=lambda kv: -kv[1]))


def is_default_routed(action: Action) -> bool:
    return bool(action.route and action.route.by_default and action.kind is Kind.DOWNLOAD)


def why(action: Action) -> tuple[str, str]:
    """(text, style) for the "Why" column: what placed the file, or what went wrong."""
    route = action.route
    if action.kind is Kind.ERROR:
        return action.reason, "red"
    if action.kind is Kind.SKIP:
        return action.reason or "skipped", "dim"
    if is_default_routed(action):
        return "nothing matched", "yellow"
    extra = f" · {action.reason or action.note}" if (action.reason or action.note) else ""
    if action.unzips:
        extra += " · unzip"
    if route and route.rule_id and route.rule_id.startswith("auto:"):
        return f"by {route.why or 'folder name'}{extra}", "dim"
    if route and route.rule_id:
        return f"your rule {route.rule_id}{extra}", "dim"
    return extra.lstrip(" ·"), "dim"


def _names(actions: list[Action], limit: int = 8) -> str:
    names = [a.file.name for a in actions if a.file][:limit]
    more = len(actions) - len(names)
    return ", ".join(names) + (f" … +{more}" if more > 0 else "")


class SyncView(View):
    """Preview table; with ``auto`` it asks to sync right away (home: "s")."""

    TITLE = "Sync"
    BINDINGS: ClassVar = [
        Binding("escape,q", "back", "Back"),
        Binding("s,enter", "start", "Sync now"),
        Binding("v", "toggle_skipped", "Show skipped"),
        Binding("i", "ignore", "Never download"),
        Binding("r", "refresh", "Refresh"),
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
    ]

    def __init__(self, auto: bool = False) -> None:
        super().__init__()
        self.auto = auto
        self.plan: SyncPlan | None = None
        self.show_skipped = False
        self.running = False
        self.cancel = False
        self.date_changes: list[Change] = []

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Fetching course data from Stud.IP…", id="status", classes="status")
        yield DataTable(id="plan", cursor_type="row", zebra_stripes=True)
        yield ProgressBar(id="progress", show_eta=False, show_percentage=False)
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(" ", "Course", "On Stud.IP", "→ Saved as", "Why")
        self.query_one("#progress", ProgressBar).display = False
        self.query_one(RichLog).display = False
        self.load_plan()

    @work(thread=True, exclusive=True)
    def load_plan(self) -> None:
        self.busy(text="Asking Stud.IP for your courses…")

        def progress(done: int, total: int, title: str) -> None:
            self.busy(
                done, total, f"Reading course [b]{done + 1}/{total}[/b]: {esc(clip(title, 50))}…"
            )

        try:
            with self.client() as client, State(paths.state_db()) as state:
                plan = build_plan(client, self.cfg, state, on_course=progress)
        except ApiError as exc:
            self.idle()
            self.check_login(exc)
            self.app.call_from_thread(self.status, f"[red]✗ {esc(exc)}[/red]")
            return
        self.idle()
        self.app.call_from_thread(self.show_plan, plan)

    def show_plan(self, plan: SyncPlan) -> None:
        self.plan = plan
        table = self.query_one(DataTable)
        table.clear()
        self.row_actions: list[Action] = []

        def order(a: Action) -> int:  # problems first, then what needs a look
            if a.kind is Kind.ERROR:
                return 0
            if is_default_routed(a):
                return 1
            return 3 if a.kind is Kind.SKIP else 2

        for course_plan in plan.plans:
            name = course_plan.course.name or course_plan.course.target
            for a in sorted(course_plan.actions, key=order):
                if a.kind is Kind.SKIP and not self.show_skipped:
                    continue
                remote = f"{a.file.folder_path}/{a.file.name}".lstrip("/") if a.file else ""
                local = rel(a.dest, course_plan.course_dir) if a.dest else ""
                note, note_style = why(a)
                flagged = is_default_routed(a)
                row_style = "yellow" if flagged else ("dim" if a.kind is Kind.SKIP else "")
                self.row_actions.append(a)
                table.add_row(
                    Text("!" if flagged else SYMBOL[a.kind], style=STYLE[a.kind]),
                    Text(clip(name, 24), style=row_style or course_color(course_plan.course.id)),
                    Text(clip(remote, 40), style=row_style),
                    Text(clip(local, 40), style=row_style),
                    Text(note, style=note_style),
                )
        todo = self._todo()
        sem = plan.semester.title if plan.semester else "all semesters"
        lines: list[str] = []
        counts = summarize(plan)
        if counts.get("error"):
            lines.append(
                f"[red]✗ [b]{esc(counts['error'])}[/b] problem(s)[/red] [dim]- at the top[/dim]"
            )
        if counts.get("default-routed"):
            n = counts["default-routed"]
            lines.append(
                f"[yellow]! [b]{esc(n)}[/b] {'file goes' if n == 1 else 'files go'} to "
                f"[b]{esc(self.cfg.default_target)}[/b][/yellow] [dim]- no match[/dim]"
            )
        skipped = skipped_reasons(plan)
        if skipped:
            detail = ", ".join(f"{n} {r}" for r, n in skipped.items())
            toggle = "v hides them" if self.show_skipped else "v shows them"
            lines.append(
                f"[dim]◎ {esc(sum(skipped.values()))} not downloaded ({esc(detail)}) - "
                f"{toggle}[/dim]"
            )
        if plan.errors:
            names = ", ".join(n for n, _ in plan.errors)
            lines.append(
                f"[red]Could not read {len(plan.errors)} course(s): {esc(names)}[/red] "
                "[dim](the others are fine; details in the log)[/dim]"
            )
        if plan.closed:
            lines.append(
                f"[dim]◎ files closed to you: {esc(', '.join(clip(n, 30) for n in plan.closed))}"
                "[/dim]"
            )
        if plan.unconfigured:
            lines.append(
                f"[yellow][b]{len(plan.unconfigured)}[/b] new course(s) not set up yet[/yellow] "
                "[dim]- c on the home screen[/dim]"
            )
        extra = "".join(f"\n{line}" for line in lines)
        if todo:
            self.status(
                f"{plan_summary_short(plan)} [dim]· {esc(sem)}[/dim]  → [b]Enter[/b] syncs{extra}"
            )
        else:
            self.status(f"[green]✓ Everything is up to date.[/green] {esc(sem)}{extra}")
        if self.auto and todo:
            self.auto = False
            self.action_start()

    def _todo(self) -> int:
        if not self.plan:
            return 0
        return sum(
            1 for p in self.plan.plans for a in p.actions if a.kind not in (Kind.SKIP, Kind.ERROR)
        )

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_start()

    def action_ignore(self) -> None:
        """Never download the selected file (or download it again, if ignored)."""
        table = self.query_one(DataTable)
        rows = getattr(self, "row_actions", [])
        if self.running or not rows or table.cursor_row >= len(rows):
            return
        action = rows[table.cursor_row]
        if action.file is None:
            return
        with State(paths.state_db()) as state:
            if action.file.id in state.ignored_ids():
                state.unignore(action.file.id)
                self.notify(f"{esc(action.file.name)} will be synced again.")
            else:
                state.ignore(action.file.id, action.course_id, action.file.name)
                self.notify(f"{esc(action.file.name)} will never be downloaded.")
        self.action_refresh()

    def action_toggle_skipped(self) -> None:
        self.show_skipped = not self.show_skipped
        if self.plan:
            self.show_plan(self.plan)

    def after_login(self) -> None:
        self.action_refresh()

    def action_refresh(self) -> None:
        if not self.running:
            self.status("Fetching course data from Stud.IP…")
            self.load_plan()

    def action_back(self) -> None:
        if self.running:
            self.cancel = True
            self.status("[yellow]Stopping after the current file…[/yellow]")
        else:
            self.app.pop_screen()

    def action_start(self) -> None:
        if self.running or not self._todo() or self.plan is None:
            return

        def confirmed(ok: bool | None) -> None:
            if ok:
                self.run_sync()

        self.app.push_screen(ConfirmScreen("Sync now?", plan_summary(self.plan)), confirmed)

    @work(thread=True, exclusive=True)
    def run_sync(self) -> None:
        assert self.plan is not None
        self.running = True
        self.cancel = False
        self.app.call_from_thread(self._prepare_run, self._todo())

        def progress(action: Action) -> None:
            self.app.call_from_thread(self._log_action, action)
            if self.cancel:
                raise KeyboardInterrupt

        try:
            with self.client() as client, State(paths.state_db()) as state:
                result = execute_plan(self.plan, self.cfg, state, client, on_progress=progress)
                if self.cfg.watch_dates:
                    courses = [
                        (p.course.id, p.course.name or p.course.target) for p in self.plan.plans
                    ]
                    self.date_changes = check_dates(
                        client, state, self.counted(courses, lambda c: f"dates of {c[1]}")
                    )
        except KeyboardInterrupt:
            self.app.call_from_thread(
                self.status,
                "[yellow]Stopped.[/yellow] Finished files are recorded; "
                "the rest follows with the next sync.",
            )
            self.running = False
            return
        except Locked:
            self.app.call_from_thread(
                self.status,
                "[yellow]Another sync is running right now (auto-sync?).[/yellow] "
                "Try again in a moment.",
            )
            self.running = False
            return
        except ApiError as exc:
            self.check_login(exc)
            self.app.call_from_thread(self.status, f"[red]✗ {esc(exc)}[/red]")
            self.running = False
            return
        self.running = False
        self.app.call_from_thread(self._finish_run, result)

    def _prepare_run(self, total: int) -> None:
        self.query_one(DataTable).display = False
        bar = self.query_one("#progress", ProgressBar)
        bar.display = True
        bar.update(total=total, progress=0)
        self.query_one(RichLog).display = True
        self.status(
            f"Syncing [b]{total}[/b] change(s)…  [dim]Esc stops after the current file[/dim]"
        )

    def _log_action(self, action: Action) -> None:
        self.query_one("#progress", ProgressBar).advance(1)
        name = action.dest.name if action.dest else ""
        self.query_one(RichLog).write(
            f"[{STYLE[action.kind]}]{SYMBOL[action.kind]}[/] {esc(clip(name, 60))}"
            + ("" if action.kind is Kind.DOWNLOAD else f"  [dim]{action.kind.value}[/]")
        )

    def _finish_run(self, result) -> None:
        log = self.query_one(RichLog)
        new = sum(1 for a in result.outcome.done if a.transfers)
        log.write("")
        failed_count = len(result.outcome.failed)
        log.write(
            f"[b]Done.[/b] [b]{esc(new)}[/b] downloaded"
            + (f", [red][b]{esc(failed_count)}[/b] failed[/red]" if failed_count else "")
            + (
                f", {esc(len(result.outcome.extracted))} archive(s) extracted"
                if result.outcome.extracted
                else ""
            )
            + "."
        )
        for action, msg in result.outcome.failed:
            log.write(f"[red]✗ {esc(action.file.name if action.file else '')}: {esc(msg)}[/red]")
        failed_ids = {id(a) for a, _ in result.outcome.failed}
        if self.plan is not None:
            for course_plan in self.plan.plans:
                label = course_plan.course.name or course_plan.course.target
                default_folder = self.cfg.course_default_target(course_plan.course)
                defaulted = [
                    a
                    for a in course_plan.actions
                    if is_default_routed(a) and id(a) not in failed_ids
                ]
                if defaulted:
                    log.write(
                        f"[yellow]! [b {course_color(course_plan.course.id)}]{esc(clip(label, 30))}"
                        f"[/]: [b]{len(defaulted)}[/b] in "
                        f"[b]{esc(default_folder)}[/b][/yellow] [dim]{esc(_names(defaulted))}[/dim]"
                    )
                by_reason: dict[str, list[Action]] = {}
                for a in course_plan.actions:
                    if a.kind is Kind.SKIP:
                        by_reason.setdefault(a.reason or "skipped", []).append(a)
                for reason, actions in by_reason.items():
                    log.write(
                        f"[dim]◎ {esc(clip(label, 30))}: {len(actions)} not downloaded, "
                        f"{esc(reason)}: {esc(_names(actions))}[/dim]"
                    )
        for m in result.merges:
            if m.written:
                log.write(f"[green]✓[/green] merged slides: {esc(m.output)}")
        for line in unlocked_lines(result.outcome):
            log.write(line)
        for change in self.date_changes:
            log.write(f"[yellow]● date[/yellow] {esc(change.text())}")
        with State(paths.state_db()) as state:
            to_move = len(pending_moves(self.cfg, state))
        if to_move:
            log.write(reorganize_hint(to_move))
        failed = len(result.outcome.failed)
        self.status(
            f"[green]✓ [b]Sync finished[/b][/green] - [b]{esc(new)}[/b] new  [dim]Esc: back[/dim]"
            if not failed
            else f"[yellow][b]Sync finished with {esc(failed)} problem(s)[/b][/yellow] - "
            "retried next time"
        )
        self.app.refresh_home(remote_changed=True)  # type: ignore[attr-defined]


# -- courses ----------------------------------------------------------------------------


class CoursesView(View):
    TITLE = "Courses"
    HINT = "Space on/off · Enter folder · a all semesters"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("space", "toggle_course", "On/off"),
        Binding("enter", "choose_folder", "Folder"),
        Binding("a", "toggle_all_semesters", "All semesters"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[CourseEntry] = []
        self.all_semesters = False
        self.label = ""

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Loading…", id="status", classes="status")
        yield DataTable(id="courses", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Sync", "Course", "Semester", "→ Folder")
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        self.busy(text="Asking Stud.IP for your courses…")
        try:
            with self.client() as client:
                sem = resolve_semester(client, "all" if self.all_semesters else "current")
                entries, missing = list_courses(client, self.cfg, sem)
        except ApiError as exc:
            self.idle()
            self.check_login(exc)
            self.app.call_from_thread(self.status, f"[red]{esc(exc)}[/red]")
            return
        label = sem.title if sem else "all semesters"
        self.idle()
        self.app.call_from_thread(self.fill, entries, label, len(missing))

    def fill(self, entries: list[CourseEntry], label: str = "", missing: int = 0) -> None:
        self.entries = entries
        self.label = label = label or self.label
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        marks = {
            CourseStatus.ACTIVE: Text("● on", style="green"),
            CourseStatus.EXCLUDED: Text("○ off", style="dim"),
            CourseStatus.UNCONFIGURED: Text("+ new", style="yellow"),
        }
        for e in entries:
            folder = e.config.target if e.config and e.config.target else ""
            is_new = e.status is CourseStatus.UNCONFIGURED
            table.add_row(
                marks[e.status],
                Text(
                    clip(e.course.title, 48),
                    style="bold"
                    if is_new
                    else (
                        "dim" if e.status is CourseStatus.EXCLUDED else course_color(e.course.id)
                    ),
                ),
                Text(e.course.semester_title, style="dim"),
                Text(clip(folder, 32)) if folder else Text("Enter: choose", style="yellow dim"),
            )
        if entries:
            table.move_cursor(row=min(cursor, len(entries) - 1))
        on = sum(1 for e in entries if e.status is CourseStatus.ACTIVE)
        new = sum(1 for e in entries if e.status is CourseStatus.UNCONFIGURED)
        note = f"{plural(on, 'course')} synced [dim]of {len(entries)} · {esc(label)}[/dim]"
        if new:
            note += f'\n[yellow][b]{new} new[/b][/yellow] - Enter: choose a folder or "don\'t sync"'
        if missing:
            note += (
                f"\n[yellow][b]{esc(missing)}[/b] set-up course(s) not found on Stud.IP[/yellow]"
            )
        self.status(note)

    def _selected(self) -> CourseEntry | None:
        table = self.query_one(DataTable)
        if not self.entries or table.cursor_row >= len(self.entries):
            return None
        return self.entries[table.cursor_row]

    def _save(self, course_id: str, **fields) -> bool:
        try:
            self.app.cfg = set_course(self.cfg.path, course_id, **fields)  # type: ignore[attr-defined]
        except (ConfigError, OSError) as exc:
            self.notify(f"Not saved: {esc(exc)}", severity="error")
            return False
        for e in self.entries:
            e.config = self.app.cfg.courses.get(e.course.id)  # type: ignore[attr-defined]
        self.fill(self.entries)
        self.app.refresh_home()  # type: ignore[attr-defined]
        return True

    def action_toggle_course(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        if entry.config is None:
            self.action_choose_folder()
        elif entry.config.exclude:
            if not entry.config.target:
                self.action_choose_folder()
            else:
                self._save(entry.course.id, exclude=None)
        else:
            self._save(entry.course.id, exclude=True)

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_choose_folder()

    def action_choose_folder(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        folders = local_course_folders(self.cfg.root)
        proposal = propose_target(entry.course.title, folders)
        ordered = ([proposal] if proposal else []) + [f for f in folders if f != proposal]
        choices: list[tuple[str, str]] = [
            (f, f"{esc(f)}  [dim](suggested)[/dim]" if f == proposal else esc(f)) for f in ordered
        ]
        new_folder = course_folder_name(entry.course.title)
        if new_folder not in folders:
            choices.append((NEW_FOLDER, f"[green]+ new folder[/green] {esc(new_folder)}"))
        choices.append((EXCLUDE, "[dim]don't sync this course[/dim]"))

        def chosen(folder: str | None) -> None:
            if folder is None:  # cancelled
                return
            if folder == EXCLUDE:
                self._save(entry.course.id, name=entry.course.title, exclude=True)
                return
            if folder == NEW_FOLDER:
                try:
                    create_course_folder(self.cfg.root, new_folder, self.cfg.course_folders)
                except OSError as exc:
                    self.notify(f"Folder not created: {esc(exc)}", severity="error")
                    return
                folder = new_folder
            self._save(entry.course.id, name=entry.course.title, target=folder, exclude=None)

        self.app.push_screen(
            ChoiceScreen(
                f"Folder for “{esc(entry.course.title)}”", choices, f"inside {esc(self.cfg.root)}"
            ),
            chosen,
        )

    def action_toggle_all_semesters(self) -> None:
        self.all_semesters = not self.all_semesters
        self.status("Loading…")
        self.load()


EXCLUDE = "\0exclude"  # cannot be a folder name
NEW_FOLDER = "\0new"


# -- history ----------------------------------------------------------------------------


class HistoryView(View):
    TITLE = "What's new"
    HINT = "Enter open · r show in folder · Space preview"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("o", "open", "Open"),
        Binding("r", "reveal", "Reveal"),
        Binding("space", "preview", "Preview"),
        Binding("i", "ignore", "Never download"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static(id="status", classes="status")
        yield DataTable(id="history", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("When", "Course", "File", "In")
        with State(paths.state_db()) as state:
            records = state.recent_files(300)
        courses = {c.id: c for c in self.cfg.courses.values()}
        today = datetime.now().astimezone().date()
        week = 0
        for r in records:
            p = Path(r.local_path)
            try:
                synced = datetime.fromisoformat(r.synced_at).astimezone()
            except ValueError:
                synced = None
            if synced is None:
                when = r.synced_at[:10]
            elif synced.date() == today:
                when = f"today {synced:%H:%M}"
            elif (today - synced.date()).days == 1:
                when = f"yesterday {synced:%H:%M}"
            else:
                when = f"{synced:%d.%m.%Y}"
            if synced is not None and (today - synced.date()).days < 7:
                week += 1
            course = courses.get(r.course_id)
            name = (course.name or course.target) if course else ""
            inside = (
                rel(p.parent, self.cfg.course_dir(course))
                if course
                else rel(p.parent, self.cfg.root)
            )
            if inside == ".":
                inside = ""
            table.add_row(
                Text(when, style="bold" if when.startswith("today") else "dim"),
                Text(clip(name, 26), style=f"bold {course_color(r.course_id)}"),
                Text(clip(p.name, 48)),
                Text(clip(inside, 28), style="dim"),
                key=r.local_path,
            )
        if not records:
            table.add_row("", "", "Nothing synced yet.", "")
        self.status(
            f"{plural(week, 'file')} in the last 7 days [dim]· newest first[/dim]"
            if records
            else "Nothing synced yet - press s on the home screen."
        )

    def _selected(self) -> Path | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return Path(key) if key else None

    def action_ignore(self) -> None:
        """Keep the local file but never download it again (also not after deleting it)."""
        path = self._selected()
        if path is None:
            return
        with State(paths.state_db()) as state:
            record = next((r for r in state.all() if r.local_path == str(path)), None)
            if record is None:
                return
            state.ignore(record.file_id, record.course_id, record.remote_name)
        self.notify(f"{esc(path.name)}: never downloaded again, even if you delete it.")

    def _run(self, *args: str) -> None:
        path = self._selected()
        if path is None:
            return
        if not path.exists():
            self.notify("The file is no longer there (moved or deleted).", severity="warning")
            return
        subprocess.Popen([*args, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @on(DataTable.RowSelected)
    def action_open(self) -> None:
        path = self._selected()
        if path is not None and path.suffix.lower() in RUNNABLE:
            # Downloads carry no quarantine flag: opening could run a program
            # uploaded to Stud.IP without any warning. Show it in the folder instead.
            self.notify(
                f"{esc(path.name)} is a program or script - not opened. Press r to show it.",
                severity="warning",
            )
            return
        self._run("open" if sys.platform == "darwin" else "xdg-open")

    def action_reveal(self) -> None:
        if sys.platform == "darwin":
            self._run("open", "-R")
        elif (p := self._selected()) is not None:
            subprocess.Popen(["xdg-open", str(p.parent)])

    def action_preview(self) -> None:
        if sys.platform == "darwin":
            self._run("qlmanage", "-p")
        else:
            self.action_open()


# -- dates ------------------------------------------------------------------------------


class DatesView(View):
    TITLE = "Dates"
    HINT = "changed dates and upcoming exams"

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield RichLog(id="dates-log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        log = self.query_one(RichLog)
        now = datetime.now().astimezone()
        courses = [(c.id, c.name or c.target) for c in self.cfg.courses.values() if not c.exclude]
        with State(paths.state_db()) as state:
            try:
                with self.client() as client:
                    check_dates(client, state, self.counted(courses, lambda c: c[1]), now)
            except ApiError as exc:
                self.idle()
                self.check_login(exc)
                self.app.call_from_thread(
                    log.write, f"[red]{esc(exc)}[/red] [dim](showing saved dates)[/dim]"
                )
            changes = [
                r
                for r in state.date_changes_since(now - timedelta(days=14))
                if datetime.fromisoformat(r["start"]) >= now
            ]
            exams = upcoming_exams(state, now)
        lines = [f"[b]Changed dates[/b] ({len(changes)}) [dim]last 14 days[/dim]"]
        lines += [f"  [yellow]●[/yellow] {esc(clip(r['text'], 100))}" for r in changes] or [
            "  [dim]none[/dim]"
        ]
        lines += ["", f"[b]Exams[/b] ({len(exams)})"]
        lines += [
            f"  [b]{esc(d.when())}[/b]  [{course_color(d.course_id)}]{esc(clip(d.course, 40))}[/]"
            + (f" [dim]· {esc(clip(d.location, 30))}[/dim]" if d.location else "")
            + f"  in [b]{esc((d.start.date() - now.date()).days)}[/b] days"
            + ("  [red][b]cancelled[/b][/red]" if d.cancelled else "")
            for d in exams
        ] or ["  [dim]none in Stud.IP yet[/dim]"]
        for line in lines:
            self.app.call_from_thread(log.write, line)


# -- news -------------------------------------------------------------------------------


class NewsView(View):
    TITLE = "Announcements"
    HINT = "a show read ones too"
    BINDINGS: ClassVar = [*View.BINDINGS, Binding("a", "toggle_all", "Show all")]

    def __init__(self) -> None:
        super().__init__()
        self.items: list[News] = []
        self.show_all = False

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal():
            yield OptionList(id="news-list")
            with VerticalScroll(id="news-body"):
                yield Static("Loading…", id="news-text")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        items: list[News] = []
        try:
            with self.client() as client:
                active = [c for c in self.cfg.courses.values() if not c.exclude]
                for c in self.counted(active, lambda c: c.name or c.target):
                    items += fetch_news(client, c.id, c.name or c.target)
        except ApiError as exc:
            self.idle()
            self.check_login(exc)
            self.app.call_from_thread(
                self.query_one("#news-text", Static).update, f"[red]{esc(exc)}[/red]"
            )
            return
        with State(paths.state_db()) as state:
            shown = items if self.show_all else unread(state, items)
        self.app.call_from_thread(self.fill, shown)

    def fill(self, items: list[News]) -> None:
        self.items = items
        options = self.query_one(OptionList)
        options.clear_options()
        for n in items:
            when = n.published.strftime("%d.%m.") if n.published else ""
            options.add_option(
                Option(
                    f"[dim]{esc(when)}[/dim] [b {course_color(n.course_id)}]"
                    f"{esc(clip(n.course, 18))}[/] "
                    f"{esc(clip(n.title, 40))}"
                )
            )
        self.query_one("#news-text", Static).update(
            "[green]✓ No unread announcements.[/green] [dim]a shows the read ones[/dim]"
            if not items
            else ""
        )
        if items:
            options.highlighted = 0

    @on(OptionList.OptionHighlighted)
    def show(self, event: OptionList.OptionHighlighted) -> None:
        if not self.items or event.option_index >= len(self.items):
            return
        n = self.items[event.option_index]
        when = n.published.strftime("%d.%m.%Y %H:%M") if n.published else ""
        self.query_one("#news-text", Static).update(
            f"[b]{esc(n.title)}[/b]\n[dim]{esc(n.course)} · {esc(when)}[/dim]\n\n{esc(n.text)}"
        )
        with State(paths.state_db()) as state:
            state.mark_news_seen([(n.id, n.course_id)])
        self.app.refresh_home(remote_changed=True)  # type: ignore[attr-defined]

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.load()


# -- rules ------------------------------------------------------------------------------


class RulesView(View):
    TITLE = "Rules"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("w", "write", "Add selected suggestions"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.suggestions: list[tuple[str, Suggestion]] = []
        self.skipped: list[tuple[str, str]] = []  # (course, why) not looked at

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with TabbedContent(id="rule-tabs"):
            with TabPane("Suggestions", id="tab-suggest"):
                yield Static("Looking at your Stud.IP folders…", id="status", classes="status")
                yield SelectionList[int](id="suggestions")
            with TabPane("Test a file name", id="tab-test"), Vertical(classes="form"):
                yield Label("Course (part of the name or folder)")
                yield Input(id="test-course", placeholder="e.g. networks")
                yield Label("File name on Stud.IP")
                yield Input(id="test-name", placeholder="e.g. Blatt 3.pdf")
                yield Label("Stud.IP folder (optional)")
                yield Input(id="test-folder", placeholder="e.g. Übungsblätter")
                yield Static(id="test-result", classes="status")
            with TabPane("Your rules", id="tab-list"), VerticalScroll():
                yield Static(id="rule-list")
        yield Footer()

    def on_mount(self) -> None:
        self.render_rule_list()
        self.load()

    def render_rule_list(self) -> None:
        lines = []
        for c in self.cfg.courses.values():
            if c.exclude:
                continue
            lines.append(
                f"[b {course_color(c.id)}]{esc(clip(c.name or c.target, 50))}[/]  "
                f"[dim]→ {esc(c.target)}[/dim]"
            )
            for i, r in enumerate(c.rules, 1):
                m = r.match
                what = (
                    " ".join(
                        f"{k}={v}"
                        for k, v in (
                            ("folder", m.folder),
                            ("name", m.name_glob),
                            ("regex", m.name_regex.pattern if m.name_regex else None),
                            ("ext", ",".join(m.ext) or None),
                        )
                        if v is not None
                    )
                    or "everything"
                )
                target = "[dim]skip[/dim]" if r.skip else r.target
                rename = f"  [dim]rename {esc(r.rename)}[/dim]" if r.rename else ""
                lines.append(
                    f"  {esc(i)}. [b]{esc(r.id)}[/b]: {esc(clip(what, 60))} → [b]{esc(target)}[/b]"
                    f"{rename}"
                )
            if not c.rules:
                lines.append("  [dim]no rules - folder and file names decide[/dim]")
            lines.append(f"  [dim]otherwise → {esc(self.cfg.course_default_target(c))}[/dim]\n")
        lines.append("[dim]Rules live in the config file: Settings, then e.[/dim]")
        self.query_one("#rule-list", Static).update("\n".join(lines))

    @work(thread=True)
    def load(self) -> None:
        found: list[tuple[str, Suggestion]] = []
        self.skipped = []
        try:
            with self.client() as client:
                active = [c for c in self.cfg.courses.values() if not c.exclude]
                for c in self.counted(active, lambda c: c.name or c.target):
                    known = {r.match.folder for r in c.rules if r.match.folder}
                    try:
                        files = client.files(c.id)
                    except AuthFailed:
                        raise
                    except ApiError as exc:  # one course must not hide the others
                        self.skipped.append(
                            (
                                c.name or c.target,
                                "files closed to you" if exc.no_access else str(exc),
                            )
                        )
                        continue
                    for s in suggest_rules(files, self.cfg.course_dir(c), {r.id for r in c.rules}):
                        if s.rule["match"].get("folder") not in known:
                            found.append((c.id, s))
        except ApiError as exc:
            self.idle()
            self.check_login(exc)
            self.app.call_from_thread(self.status, f"[red]{esc(exc)}[/red]")
            return
        self.app.call_from_thread(self.fill, found)

    def fill(self, found: list[tuple[str, Suggestion]]) -> None:
        self.suggestions = found
        names = {c.id: c.name or c.target for c in self.cfg.courses.values()}
        selection = self.query_one(SelectionList)
        selection.clear_options()
        for i, (cid, s) in enumerate(found):
            match = s.rule["match"]
            what = (
                f"folder “{match['folder'].strip('*')}”"
                if match.get("folder")
                else f"file names like {s.category}"
            )
            selection.add_option(
                (
                    f"[b {course_color(cid)}]{esc(clip(names[cid], 28))}[/]: "
                    f"{esc(clip(what, 40))} → "
                    f"[b]{esc(s.rule['target'])}[/b]  [dim]({s.files} "
                    f"file{'s' if s.files != 1 else ''})[/dim]",
                    i,
                    True,
                )
            )
        text = (
            f"{plural(len(found), 'suggestion')} - [b]w[/b] adds the ticked ones "
            "[dim](Space ticks)[/dim]"
            if found
            else "[green]✓ No new suggestions[/green] [dim]- your Stud.IP folders are covered[/dim]"
        )
        if self.skipped:
            skipped = ", ".join(clip(name, 30) for name, _ in self.skipped)
            reasons = {why for _, why in self.skipped}
            reason = reasons.pop() if len(reasons) == 1 else "details in the log"
            text += f"\n[dim]◎ not looked at: {esc(skipped)} ({esc(reason)})[/dim]"
        self.status(text)

    def action_write(self) -> None:
        chosen = self.query_one(SelectionList).selected
        if not chosen:
            return

        def write(ok: bool | None) -> None:
            if not ok:
                return
            blocks = [
                rule_block(cid, s.rule, s.reason)
                for i, (cid, s) in enumerate(self.suggestions)
                if i in chosen
            ]
            try:
                append_blocks(self.cfg.path, blocks, "rules added in the TUI")
                self.app.reload_config()  # type: ignore[attr-defined]
            except Exception as exc:
                self.notify(f"Could not write: {esc(exc)}", severity="error")
                return
            self.notify(f"Added {len(blocks)} rule(s).")
            self.render_rule_list()
            self.fill([])
            self.load()

        self.app.push_screen(ConfirmScreen(f"Add {len(chosen)} rule(s) to your config?"), write)

    @on(Input.Changed)
    def test_changed(self) -> None:
        query = self.query_one("#test-course", Input).value.strip().casefold()
        name = self.query_one("#test-name", Input).value.strip()
        folder = self.query_one("#test-folder", Input).value.strip()
        result = self.query_one("#test-result", Static)
        if not query or not name:
            result.update("[dim]Type a course and a file name to see where it would go.[/dim]")
            return
        hits = [
            c
            for c in self.cfg.courses.values()
            if not c.exclude and (query in c.name.casefold() or query in c.target.casefold())
        ]
        if len(hits) != 1:
            result.update(
                f"[yellow]{esc(len(hits))} courses match - type more of the name.[/yellow]"
                if hits
                else "[yellow]No active course matches.[/yellow]"
            )
            return
        course = hits[0]
        file = RemoteFile(
            id="test",
            course_id=course.id,
            name=name,
            folder_path=folder,
            size=100_000,
            chdate=datetime.now().astimezone(),
            mime_type="application/octet-stream",
            description="",
            downloadable=True,
        )
        try:
            route = build_router(self.cfg, course)(file)
        except RenameError as exc:
            result.update(f"[red]{esc(exc)}[/red]")
            return
        if route.skip:
            result.update(f"Rule [b]{esc(route.rule_id)}[/b]: not downloaded (skip)")
            return
        where = f"{esc(course.target)}/[b]{esc(route.subdir)}[/b]/{esc(route.name)}"
        if route.by_default:
            reason = "[yellow]nothing matched - default folder[/yellow]"
        elif route.rule_id and route.rule_id.startswith("auto:"):
            reason = f"by {esc(route.why or 'folder name')}"
        else:
            reason = f"your rule [b]{esc(route.rule_id)}[/b]"
        result.update(f"→ {where}\n[dim]{reason}[/dim]")


# -- reorganize / merge / doctor --------------------------------------------------------


class ReorganizeView(View):
    TITLE = "Reorganize"
    HINT = "move synced files to where they belong now"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("enter", "apply", "Move files"),
        Binding("u", "undo", "Undo last"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.moves: list[Move] = []

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static(id="status", classes="status")
        yield DataTable(id="moves", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Now", "→ Moves to", "Problem")
        self.reload()

    def reload(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        with State(paths.state_db()) as state:
            self.moves = plan_reorganize(self.cfg, state)
        for m in self.moves:
            table.add_row(
                Text(clip(rel(m.src, self.cfg.root), 45), style="dim"),
                Text(clip(rel(m.dst, self.cfg.root), 45)),
                Text(clip(m.problem, 30), style="yellow"),
            )
        ok = sum(1 for m in self.moves if not m.problem)
        table.display = bool(self.moves)
        self.status(
            f"{plural(ok, 'file')} can move - [b]Enter[/b] moves them [dim](u undoes)[/dim]"
            if ok
            else "[green]✓ [b]Everything is in place.[/b][/green]  "
            "[dim]u undoes the last move[/dim]"
        )

    @on(DataTable.RowSelected)
    def action_apply(self) -> None:
        ok = sum(1 for m in self.moves if not m.problem)
        if not ok:
            return

        def apply(confirmed: bool | None) -> None:
            if not confirmed:
                return
            with State(paths.state_db()) as state:
                done, failed = apply_reorganize(self.moves, state)
            self.notify(f"Moved {done} file(s)" + (f", {len(failed)} failed" if failed else ""))
            self.reload()

        self.app.push_screen(ConfirmScreen(f"Move {ok} file(s)?", "Undo with u."), apply)

    def action_undo(self) -> None:
        with State(paths.state_db()) as state:
            run_id, steps = plan_undo(state)
        moves = [s for s in steps if s.op == "move" and not s.problem]
        if run_id is None or not moves:
            self.notify("Nothing to undo.")
            return

        def undo(ok: bool | None) -> None:
            if not ok:
                return
            with State(paths.state_db()) as state:
                n = apply_undo(state, run_id, steps)
            self.notify(f"Moved {n} file(s) back.")
            self.reload()

        self.app.push_screen(ConfirmScreen(f"Move {len(moves)} file(s) back?"), undo)


class MergeView(View):
    """Set up and run slide merging per course, without touching the config file."""

    TITLE = "Merge slides"
    HINT = "Enter choose the slides folder · m merge now"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("enter", "choose", "Choose folder"),
        Binding("m", "merge", "Merge now"),
    ]
    OUTPUT = "All slides.pdf"

    def __init__(self) -> None:
        super().__init__()
        self.courses: list = []
        self.results: dict[str, MergeOutcome] = {}

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static(id="status", classes="status")
        yield DataTable(id="merges", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Course", "Slides from", "→ One PDF", "Last run")
        self.fill()
        if any(c.merge for c in self.courses):
            self.action_merge()

    def fill(self) -> None:
        self.courses = [c for c in self.cfg.courses.values() if not c.exclude]
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        for c in self.courses:
            result = self.results.get(c.id)
            if c.merge is None:
                source, output = Text("Enter: choose", style="yellow dim"), Text("")
            else:
                source = Text(clip(c.merge.source, 30), style="bold")
                output = Text(clip(c.merge.output, 30))
            last = Text("")
            if result is not None:
                last = Text(
                    ("✓ " if result.written else "◎ ") + clip(result.reason, 40),
                    style="green" if result.written else "dim",
                )
            table.add_row(
                Text(clip(c.name or c.target, 36), style=course_color(c.id)), source, output, last
            )
        if self.courses:
            table.move_cursor(row=min(cursor, len(self.courses) - 1))
        on = sum(1 for c in self.courses if c.merge)
        self.status(
            f"{plural(on, 'course')} merged into one PDF "
            f"[dim]of {len(self.courses)} - after each sync too[/dim]\n"
            "[b]Enter[/b] chooses a course's slides folder, [b]m[/b] merges now"
            if self.courses
            else "No courses set up yet - Courses on the home screen."
        )

    def _selected(self):
        table = self.query_one(DataTable)
        if not self.courses or table.cursor_row >= len(self.courses):
            return None
        return self.courses[table.cursor_row]

    @on(DataTable.RowSelected)
    def action_choose(self) -> None:
        course = self._selected()
        if course is None:
            return
        subdirs = local_subdirs(self.cfg.course_dir(course))
        slides = local_category_dirs(subdirs, "slides")
        ordered = slides + [d for d in subdirs if d not in slides]
        choices: list[tuple[str, str]] = [
            (d, f"{esc(d)}  [dim](slides)[/dim]" if d in slides else esc(d)) for d in ordered
        ]
        if course.merge is not None:
            choices.append((NO_MERGE, "[dim]don't merge this course[/dim]"))
        if not ordered:
            self.notify("This course folder has no subfolders yet - sync first.")
            return

        def chosen(source: str | None) -> None:
            if source is None:
                return
            value = (
                None
                if source == NO_MERGE
                else {
                    "source": source,
                    "output": course.merge.output if course.merge else self.OUTPUT,
                }
            )
            try:
                self.app.cfg = set_course(self.cfg.path, course.id, merge=value)  # type: ignore[attr-defined]
            except (ConfigError, OSError) as exc:
                self.notify(f"Not saved: {esc(exc)}", severity="error")
                return
            self.results.pop(course.id, None)
            self.fill()
            if value is not None:
                self.action_merge()

        self.app.push_screen(
            ChoiceScreen(
                f"Slides of “{esc(clip(course.name or course.target, 40))}”",
                choices,
                f"Every PDF in this folder goes into [b]{esc(self.OUTPUT)}[/b] there, "
                "in file name order. Your files stay as they are.",
            ),
            chosen,
        )

    def action_merge(self) -> None:
        self.run_merge()

    @work(thread=True, exclusive=True)
    def run_merge(self) -> None:
        todo = [c for c in self.cfg.courses.values() if c.merge and not c.exclude]
        with State(paths.state_db()) as state:
            for course in self.counted(todo, lambda c: c.name or c.target):
                for result in run_merges(self.cfg, state, [course]):
                    self.results[course.id] = result
        self.app.call_from_thread(self.fill)


NO_MERGE = "\0none"


class DoctorView(View):
    TITLE = "Doctor"

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield RichLog(id="doctor-log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_checks()

    @work(thread=True)
    def run_checks(self) -> None:
        marks = {
            "ok": "[green]✓[/green]",
            "info": "[blue]i[/blue]",
            "warn": "[yellow]![/yellow]",
            "fail": "[red]✗[/red]",
        }
        log = self.query_one(RichLog)
        self.busy()
        checks = list(doctor.run_checks())
        self.idle()
        rank = {"fail": 0, "warn": 1, "info": 2, "ok": 3}
        counts = {level: sum(1 for c in checks if c.level == level) for level in rank}
        summary = (
            "[green]✓ [b]All checks passed[/b][/green]"
            if not counts["fail"] and not counts["warn"]
            else " · ".join(
                part
                for part in (
                    f"[red][b]{counts['fail']} failed[/b][/red]" if counts["fail"] else "",
                    f"[yellow][b]{counts['warn']} warning(s)[/b][/yellow]"
                    if counts["warn"]
                    else "",
                    f"[b]{counts['ok']}[/b] ok",
                )
                if part
            )
        )
        lines = [summary, ""]
        # course folders that are fine: one line instead of one per course
        ok_courses = [c for c in checks if c.level == "ok" and c.name.startswith("course ")]
        for check in sorted(checks, key=lambda c: rank.get(c.level, 9)):
            if check in ok_courses:
                if check is ok_courses[0]:
                    names = ", ".join(c.name.removeprefix("course ") for c in ok_courses)
                    lines.append(
                        f"{marks['ok']} [b]{len(ok_courses)} course folders[/b]  "
                        f"[dim]{esc(clip(names, 90))}[/dim]"
                    )
                continue
            detail = short_path(check.detail)
            if check.level == "ok":
                detail = clip(detail, 90)
            dim = check.level in ("ok", "info")
            lines.append(
                f"{marks[check.level]} [b]{esc(check.name)}[/b]  "
                + (f"[dim]{esc(detail)}[/dim]" if dim else esc(detail))
            )
        for line in lines:
            self.app.call_from_thread(log.write, line)


def short_path(text: str) -> str:
    """Long iCloud and home paths in a text, readable: "iCloud Drive/Uni/…", "~/…"."""
    home = str(Path.home())
    icloud = f"{home}/Library/Mobile Documents/com~apple~CloudDocs"
    return text.replace(icloud, "iCloud Drive").replace(home, "~")
