"""Interactive terminal UI (Textual). Run `studip` without a command.

Every view is a thin layer over the same functions the CLI uses; nothing here
changes files without an explicit confirmation.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Input,
    Label,
    OptionList,
    ProgressBar,
    RichLog,
    SelectionList,
    Static,
)
from textual.widgets.option_list import Option

from . import __version__, auth, doctor, paths, schedule
from .api import ApiError, AuthFailed, StudipClient
from .config import Config, ConfigError, load
from .news import News, fetch_news, unread
from .planner import Action, Kind
from .reorganize import Move, apply_reorganize, plan_reorganize
from .report import STYLE, SYMBOL, summarize
from .state import State
from .suggest import Suggestion, suggest_rules
from .sync import SyncPlan, build_plan, execute_plan, run_merges
from .tomlwrite import append_blocks, rule_block

LOGO = r"""
 ┌─┐┌┬┐┬ ┬┌┬┐┬┌─┐  ┬─┐┌─┐┬ ┬┌┬┐┌─┐┬─┐
 └─┐ │ │ │ │││├─┘  ├┬┘│ ││ │ │ ├┤ ├┬┘
 └─┘ ┴ └─┘─┴┘┴┴    ┴└─└─┘└─┘ ┴ └─┘┴└─"""


@dataclass(frozen=True)
class MenuItem:
    key: str
    title: str
    description: str


MENU = [
    MenuItem(
        "sync",
        "Sync",
        "Preview what would be downloaded, then sync.\n\n"
        "New files are routed by your rules, renamed and never overwrite anything.",
    ),
    MenuItem(
        "courses",
        "Courses",
        "All Stud.IP courses of the semester and whether they are "
        "synced, excluded or not configured yet.",
    ),
    MenuItem(
        "history",
        "What's new",
        "Files that arrived recently.\n\n"
        "Open them, reveal them in the file manager or preview them.",
    ),
    MenuItem("news", "Announcements", "Unread announcements of your courses."),
    MenuItem(
        "rules",
        "Rule suggestions",
        "Rules derived from Stud.IP folder names and your "
        "existing subfolders. Pick the ones you want and append them to the config.",
    ),
    MenuItem(
        "reorganize",
        "Reorganize",
        "Move already synced files to where the current rules "
        "would put them. Can be undone with `studip undo`.",
    ),
    MenuItem(
        "merge",
        "Merge slides",
        "Combine lecture PDFs into one file per course (courses with a `merge` setting).",
    ),
    MenuItem(
        "schedule",
        "Auto-sync",
        "Install or remove the background sync job (launchd/systemd) with notifications.",
    ),
    MenuItem("doctor", "Doctor", "Check config, folders, keychain, server and scheduler."),
]


# -- shared modals ----------------------------------------------------------------------


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(self.question, id="question")
            with Horizontal(id="buttons"):
                yield Button("Yes (y)", variant="primary", id="yes")
                yield Button("No (n)", id="no")

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class PasswordScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, cfg: Config, error: str = "") -> None:
        super().__init__()
        self.cfg = cfg
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"Password for [b]{self.cfg.username}[/b] at {self.cfg.base_url}")
            yield Label("It is verified and stored in the OS keychain.", classes="dim")
            if self.error:
                yield Label(self.error, classes="error")
            yield Input(password=True, id="password")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# -- base view ----------------------------------------------------------------------------


class View(Screen):
    """A full-screen view with a title bar and Escape to go back."""

    BINDINGS: ClassVar = [Binding("escape,q", "app.pop_screen", "Back")]
    TITLE = ""

    def compose_header(self) -> ComposeResult:
        yield Static(f" [b]{self.TITLE}[/b]", classes="view-title")

    @property
    def cfg(self) -> Config:
        return self.app.cfg  # type: ignore[attr-defined]

    def client(self) -> StudipClient:
        return self.app.make_client()  # type: ignore[attr-defined]


class SyncView(View):
    TITLE = "Sync"
    BINDINGS: ClassVar = [
        Binding("escape,q", "back", "Back"),
        Binding("enter,s", "start", "Start sync"),
        Binding("v", "toggle_skipped", "Show skipped"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.plan: SyncPlan | None = None
        self.show_skipped = False
        self.running = False
        self.cancel = False

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Fetching course data from Stud.IP…", id="sync-status")
        yield DataTable(id="plan", cursor_type="row", zebra_stripes=True)
        yield ProgressBar(id="progress", show_eta=False, show_percentage=False)
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(" ", "Course", "Stud.IP", "Local", "Note")
        self.query_one(ProgressBar).display = False
        self.query_one(RichLog).display = False
        self.load_plan()

    @work(thread=True, exclusive=True)
    def load_plan(self) -> None:
        try:
            with self.client() as client, State(paths.state_db()) as state:
                plan = build_plan(client, self.cfg, state)
        except ApiError as exc:
            self.app.call_from_thread(self.set_status, f"[red]✗ {exc}[/red]")
            return
        self.app.call_from_thread(self.show_plan, plan)

    def set_status(self, text: str) -> None:
        self.query_one("#sync-status", Static).update(text)

    def show_plan(self, plan: SyncPlan) -> None:
        self.plan = plan
        table = self.query_one(DataTable)
        table.clear()
        for course_plan in plan.plans:
            name = course_plan.course.name or course_plan.course.target
            for a in course_plan.actions:
                if a.kind is Kind.SKIP and not self.show_skipped:
                    continue
                remote = f"{a.file.folder_path}/{a.file.name}".lstrip("/") if a.file else ""
                local = ""
                if a.dest is not None:
                    try:
                        local = str(a.dest.relative_to(course_plan.course_dir))
                    except ValueError:
                        local = str(a.dest)
                note = a.reason or a.note
                if a.route and a.route.by_default and a.kind is Kind.DOWNLOAD:
                    note = (note + "; " if note else "") + "default target"
                if a.unzips:
                    note = (note + "; " if note else "") + "+ unzip"
                table.add_row(
                    Text(SYMBOL[a.kind], style=STYLE[a.kind]),
                    name,
                    remote,
                    local,
                    Text(note, style="dim"),
                )
        counts = summarize(plan)
        labels = {
            "download": "new",
            "adopt": "already local",
            "new-version": "new versions",
            "default-routed": "via default target",
            "unchanged": "unchanged",
        }
        parts = [f"{labels.get(k, k)}: [b]{v}[/b]" for k, v in sorted(counts.items())]
        todo = self._todo()
        hint = (
            f"  ·  [b]Enter[/b] syncs {todo} change(s)"
            if todo
            else "  ·  [green]✓ everything is up to date[/green]"
        )
        sem = plan.semester.title if plan.semester else "all semesters"
        extra = ""
        if plan.unconfigured:
            extra = f"\n[yellow]{len(plan.unconfigured)} course(s) without config entry[/yellow]"
        self.set_status(f"{sem}  ·  " + ("  ".join(parts) or "nothing to do") + hint + extra)

    def _todo(self) -> int:
        if not self.plan:
            return 0
        return sum(
            1 for p in self.plan.plans for a in p.actions if a.kind not in (Kind.SKIP, Kind.ERROR)
        )

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_start()

    def action_toggle_skipped(self) -> None:
        self.show_skipped = not self.show_skipped
        if self.plan:
            self.show_plan(self.plan)

    def action_refresh(self) -> None:
        if not self.running:
            self.set_status("Fetching course data from Stud.IP…")
            self.load_plan()

    def action_back(self) -> None:
        if self.running:
            self.cancel = True
            self.set_status("[yellow]Stopping after the current file…[/yellow]")
        else:
            self.app.pop_screen()

    def action_start(self) -> None:
        if self.running or not self._todo():
            return
        self.app.push_screen(
            ConfirmScreen(f"Sync {self._todo()} change(s) now?"),
            lambda ok: self.run_sync() if ok else None,
        )

    @work(thread=True, exclusive=True)
    def run_sync(self) -> None:
        assert self.plan is not None
        self.running = True
        total = self._todo()
        self.app.call_from_thread(self._prepare_run, total)

        def progress(action: Action) -> None:
            self.app.call_from_thread(self._log_action, action)
            if self.cancel:
                raise KeyboardInterrupt

        try:
            with self.client() as client, State(paths.state_db()) as state:
                result = execute_plan(self.plan, self.cfg, state, client, on_progress=progress)
        except KeyboardInterrupt:
            self.app.call_from_thread(
                self.set_status, "[yellow]Stopped.[/yellow] Finished files are recorded."
            )
            self.running = False
            return
        except ApiError as exc:
            self.app.call_from_thread(self.set_status, f"[red]✗ {exc}[/red]")
            self.running = False
            return
        self.running = False
        self.app.call_from_thread(self._finish_run, result)

    def _prepare_run(self, total: int) -> None:
        self.query_one(DataTable).display = False
        bar = self.query_one(ProgressBar)
        bar.display = True
        bar.update(total=total, progress=0)
        self.query_one(RichLog).display = True
        self.set_status(
            f"Syncing {total} change(s)…  [dim](Esc stops after the current file)[/dim]"
        )

    def _log_action(self, action: Action) -> None:
        self.query_one(ProgressBar).advance(1)
        name = action.dest.name if action.dest else ""
        self.query_one(RichLog).write(
            f"[{STYLE[action.kind]}]{SYMBOL[action.kind]}[/] {name}  [dim]{action.kind.value}[/]"
        )

    def _finish_run(self, result) -> None:
        log = self.query_one(RichLog)
        new = sum(1 for a in result.outcome.done if a.transfers)
        log.write("")
        log.write(
            f"[b]Done.[/b] {new} downloaded, {len(result.outcome.failed)} failed, "
            f"{len(result.outcome.extracted)} archive(s) extracted."
        )
        for action, msg in result.outcome.failed:
            log.write(f"[red]✗ {action.file.name if action.file else ''}: {msg}[/red]")
        for m in result.merges:
            if m.written:
                log.write(f"[green]✓[/green] merged slides: {m.output}")
        self.set_status("[green]✓ Sync finished.[/green]  [dim]Esc to go back[/dim]")
        self.app.refresh_header()  # type: ignore[attr-defined]


class CoursesView(View):
    TITLE = "Courses"

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Loading…", id="courses-status")
        yield DataTable(id="courses", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Status", "Course", "Semester", "Target", "ID")
        self.load()

    @work(thread=True)
    def load(self) -> None:
        from .sync import list_courses, resolve_semester

        try:
            with self.client() as client:
                sem = resolve_semester(client, self.cfg.semester)
                entries, missing = list_courses(client, self.cfg, sem)
        except ApiError as exc:
            self.app.call_from_thread(
                self.query_one("#courses-status", Static).update, f"[red]{exc}[/red]"
            )
            return

        def fill() -> None:
            table = self.query_one(DataTable)
            colours = {"active": "green", "excluded": "dim", "unconfigured": "yellow"}
            for e in entries:
                status = e.status.value
                table.add_row(
                    Text(status, style=colours[status]),
                    e.course.title,
                    e.course.semester_title,
                    e.config.target if e.config and not e.config.exclude else "",
                    Text(e.course.id, style="dim"),
                )
            note = f"{sem.title if sem else 'all semesters'} · {len(entries)} courses"
            if missing:
                note += f" · [yellow]{len(missing)} configured course(s) not found[/yellow]"
            self.query_one("#courses-status", Static).update(note)

        self.app.call_from_thread(fill)


class HistoryView(View):
    TITLE = "What's new"
    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("enter,o", "open", "Open"),
        Binding("r", "reveal", "Reveal in folder"),
        Binding("space", "preview", "Quick Look"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield DataTable(id="history", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("When", "File", "Folder")
        with State(paths.state_db()) as state:
            records = state.recent_files(200)
        root = self.cfg.root
        for r in records:
            p = Path(r.local_path)
            try:
                folder = str(p.parent.relative_to(root))
            except ValueError:
                folder = str(p.parent)
            table.add_row(
                r.synced_at.replace("T", " ")[:16],
                p.name,
                Text(folder, style="dim"),
                key=r.local_path,
            )
        if not records:
            table.add_row("", "Nothing synced yet.", "")

    def _selected(self) -> Path | None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return None
        key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        return Path(key) if key else None

    def _run(self, *args: str) -> None:
        path = self._selected()
        if path is None:
            return
        if not path.exists():
            self.notify("File no longer exists there (moved?)", severity="warning")
            return
        subprocess.Popen([*args, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_open()

    def action_open(self) -> None:
        self._run("open" if sys.platform == "darwin" else "xdg-open")

    def action_reveal(self) -> None:
        if sys.platform == "darwin":
            self._run("open", "-R")
        elif (p := self._selected()) is not None:
            subprocess.Popen(["xdg-open", str(p.parent)])

    def action_preview(self) -> None:
        if sys.platform == "darwin":
            self._run("qlmanage", "-p")


class NewsView(View):
    TITLE = "Announcements"
    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("a", "toggle_all", "Show read too"),
    ]

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
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        items: list[News] = []
        try:
            with self.client() as client:
                for c in self.cfg.courses.values():
                    if not c.exclude:
                        items += fetch_news(client, c.id, c.name or c.target)
        except ApiError as exc:
            self.app.call_from_thread(
                self.query_one("#news-text", Static).update, f"[red]{exc}[/red]"
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
            options.add_option(Option(f"{when} {n.course[:18]}: {n.title}"))
        text = self.query_one("#news-text", Static)
        text.update("No unread announcements." if not items else "Select an announcement.")
        if items:
            options.highlighted = 0

    @on(OptionList.OptionHighlighted)
    def show(self, event: OptionList.OptionHighlighted) -> None:
        if not self.items or event.option_index >= len(self.items):
            return
        n = self.items[event.option_index]
        self.query_one("#news-text", Static).update(
            f"[b]{n.title}[/b]\n[dim]{n.course}[/dim]\n\n{n.text}"
        )
        with State(paths.state_db()) as state:
            state.mark_news_seen([(n.id, n.course_id)])

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.load()


class RulesView(View):
    TITLE = "Rule suggestions"
    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("w", "write", "Append selected to config"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.suggestions: list[tuple[str, Suggestion]] = []

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Looking at your Stud.IP folders…", id="rules-status")
        yield SelectionList[int](id="suggestions")
        yield Footer()

    def on_mount(self) -> None:
        self.load()

    @work(thread=True)
    def load(self) -> None:
        found: list[tuple[str, Suggestion]] = []
        try:
            with self.client() as client:
                for c in self.cfg.courses.values():
                    if c.exclude:
                        continue
                    known = {r.match.folder for r in c.rules if r.match.folder}
                    for s in suggest_rules(
                        client.files(c.id), self.cfg.course_dir(c), {r.id for r in c.rules}
                    ):
                        if s.rule["match"].get("folder") not in known:
                            found.append((c.id, s))
        except ApiError as exc:
            self.app.call_from_thread(
                self.query_one("#rules-status", Static).update, f"[red]{exc}[/red]"
            )
            return
        self.app.call_from_thread(self.fill, found)

    def fill(self, found: list[tuple[str, Suggestion]]) -> None:
        self.suggestions = found
        names = {c.id: c.name or c.target for c in self.cfg.courses.values()}
        selection = self.query_one(SelectionList)
        for i, (cid, s) in enumerate(found):
            match = s.rule["match"]
            what = (
                f"folder “{match['folder'].rstrip('*')}”"
                if match.get("folder")
                else f"name ~ {match.get('name_regex', '')[:30]}"
            )
            selection.add_option(
                (
                    f"{names[cid]}: {what} → {s.rule['target']}  [dim]({s.files} files)[/dim]",
                    i,
                    True,
                )
            )
        status = (
            f"{len(found)} suggestion(s). Space toggles, [b]w[/b] appends the selected "
            "ones to your config (a backup is kept)."
            if found
            else "[green]✓ No new suggestions.[/green]"
        )
        self.query_one("#rules-status", Static).update(status)

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
                backup = append_blocks(self.cfg.path, blocks, "rules added in the TUI")
                self.app.reload_config()  # type: ignore[attr-defined]
            except Exception as exc:
                self.notify(f"Could not write: {exc}", severity="error")
                return
            self.notify(f"Appended {len(blocks)} rule(s). Backup: {backup.name}")
            self.app.pop_screen()

        self.app.push_screen(
            ConfirmScreen(f"Append {len(chosen)} rule(s) to {self.cfg.path.name}?"), write
        )


class ReorganizeView(View):
    TITLE = "Reorganize"
    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("enter", "apply", "Move files"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.moves: list[Move] = []

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static(id="reorg-status")
        yield DataTable(id="moves", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("From", "To", "Note")
        with State(paths.state_db()) as state:
            self.moves = plan_reorganize(self.cfg, state)
        root = self.cfg.root
        for m in self.moves:
            table.add_row(_rel(m.src, root), _rel(m.dst, root), Text(m.problem, style="yellow"))
        ok = sum(1 for m in self.moves if not m.problem)
        self.query_one(Static).update(
            f"{ok} file(s) can be moved. [b]Enter[/b] to apply; `studip undo` reverts."
            if ok
            else "[green]✓ Everything is where the rules want it.[/green]"
        )

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_apply()

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
            self.app.pop_screen()

        self.app.push_screen(ConfirmScreen(f"Move {ok} file(s)?"), apply)


class MergeView(View):
    TITLE = "Merge slides"

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield RichLog(id="merge-log", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_merge()

    @work(thread=True)
    def run_merge(self) -> None:
        with State(paths.state_db()) as state:
            results = run_merges(self.cfg, state, list(self.cfg.courses.values()))
        log = self.query_one(RichLog)
        if not results:
            self.app.call_from_thread(
                log.write,
                "No course has a merge setting. Example:\n"
                '  merge = { source = "Skript : Alle Folien", '
                'output = "Alle Folien.pdf" }',
            )
        for m in results:
            mark = "[green]✓[/green]" if m.written else "[dim]◎[/dim]"
            self.app.call_from_thread(log.write, f"{mark} {m.course}: {m.reason}")


class ScheduleView(View):
    TITLE = "Auto-sync"
    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("i", "install", "Install"),
        Binding("u", "uninstall", "Uninstall"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static(id="schedule-status")
        yield Label("Interval in minutes:")
        yield Input("120", id="interval", type="integer")
        with Horizontal(id="buttons-left"):
            yield Button("Install (i)", variant="primary", id="install")
            yield Button("Uninstall (u)", id="uninstall")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()
        self.set_focus(None)  # keep i/u working as shortcuts

    @on(Button.Pressed, "#install")
    def _install_pressed(self) -> None:
        self.action_install()

    @on(Button.Pressed, "#uninstall")
    def _uninstall_pressed(self) -> None:
        self.action_uninstall()

    def refresh_status(self) -> None:
        st = schedule.status(paths.profile())
        lines = []
        if st.installed:
            every = f"every {st.interval_minutes} min" if st.interval_minutes else "installed"
            lines.append(
                f"[green]●[/green] Installed, {every}, "
                f"{'loaded' if st.loaded else '[yellow]not loaded[/yellow]'}"
            )
            lines.append(f"[dim]{st.path}[/dim]")
        else:
            lines.append("[dim]○[/dim] Not installed. [b]i[/b] installs, [b]u[/b] removes.")
        if warning := schedule.executable_warning(schedule.executable()):
            lines.append(f"\n[yellow]! {warning}[/yellow]")
        self.query_one("#schedule-status", Static).update("\n".join(lines))

    def action_install(self) -> None:
        try:
            minutes = max(15, int(self.query_one(Input).value))
        except ValueError:
            minutes = 120

        def install(ok: bool | None) -> None:
            if not ok:
                return
            try:
                schedule.install(minutes, paths.profile())
                self.notify("Auto-sync installed")
            except (OSError, subprocess.CalledProcessError) as exc:
                self.notify(f"Could not install: {exc}", severity="error")
            self.refresh_status()

        self.app.push_screen(
            ConfirmScreen(f"Install a background sync every {minutes} minutes?"), install
        )

    def action_uninstall(self) -> None:
        def remove(ok: bool | None) -> None:
            if ok:
                schedule.uninstall(paths.profile())
                self.refresh_status()

        self.app.push_screen(ConfirmScreen("Remove the background sync job?"), remove)


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
        for check in doctor.run_checks():
            self.app.call_from_thread(
                log.write, f"{marks[check.level]} [b]{check.name}[/b]: {check.detail}"
            )


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


VIEWS: dict[str, type[Screen]] = {
    "sync": SyncView,
    "courses": CoursesView,
    "history": HistoryView,
    "news": NewsView,
    "rules": RulesView,
    "reorganize": ReorganizeView,
    "merge": MergeView,
    "schedule": ScheduleView,
    "doctor": DoctorView,
}
NEEDS_LOGIN = {"sync", "courses", "news", "rules"}


# -- main screen ----------------------------------------------------------------------------


class MainScreen(Screen):
    BINDINGS: ClassVar = [
        Binding("q", "app.quit", "Quit"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        *[Binding(str(i + 1), f"open({i})", show=False) for i in range(len(MENU))],
    ]

    def compose(self) -> ComposeResult:
        yield Static(LOGO, id="logo")
        yield Static(id="subtitle")
        with Horizontal(id="main"):
            yield OptionList(
                *[Option(f" {i + 1}  {m.title}", id=m.key) for i, m in enumerate(MENU)],
                id="menu",
            )
            yield Static(id="details")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self.app.refresh_header()  # type: ignore[attr-defined]

    @on(OptionList.OptionHighlighted, "#menu")
    def highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.query_one("#details", Static).update(MENU[event.option_index].description)

    @on(OptionList.OptionSelected, "#menu")
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.app.open_view(MENU[event.option_index].key)  # type: ignore[attr-defined]

    def action_open(self, index: int) -> None:
        self.query_one(OptionList).highlighted = index
        self.app.open_view(MENU[index].key)  # type: ignore[attr-defined]

    def action_cursor_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()


class StudipRouterApp(App):
    TITLE = "studip-router"
    CSS = """
    Screen { background: $background; }
    #logo { color: $accent; padding: 0 2; height: auto; }
    #subtitle { color: $text-muted; padding: 0 3 1 3; height: auto; }
    #main { height: 1fr; }
    #menu { width: 34; border: round $accent 40%; padding: 0 1; }
    #menu:focus { border: round $accent; }
    #details { padding: 1 3; color: $text; border: round $panel; width: 1fr; }
    .view-title { background: $accent 20%; color: $text; padding: 0 1; height: 1; }
    #sync-status, #courses-status, #rules-status, #reorg-status, #schedule-status {
        padding: 1 2; height: auto;
    }
    DataTable { height: 1fr; }
    #progress { padding: 0 2; height: 1; width: 100%; }
    #progress Bar { width: 1fr; }
    #log, #merge-log, #doctor-log { height: 1fr; border: round $panel; padding: 0 1; }
    #news-list { width: 45%; }
    #news-body { padding: 1 2; border: round $panel; }
    ConfirmScreen, PasswordScreen { align: center middle; }
    #dialog { width: 64; height: auto; padding: 1 2; border: thick $accent; background: $surface; }
    #question { margin-bottom: 1; }
    #buttons { height: auto; align-horizontal: right; }
    #buttons Button { margin-left: 2; }
    .dim { color: $text-muted; }
    .error { color: $error; }
    #interval { width: 20; margin: 0 2; }
    #buttons-left { height: auto; padding: 1 2; }
    #buttons-left Button { margin-right: 2; }
    Label { padding: 0 2; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.cfg: Config | None = None
        self.config_error = ""

    def on_mount(self) -> None:
        self.theme = "tokyo-night"
        self.reload_config()
        self.push_screen(MainScreen())

    def reload_config(self) -> None:
        try:
            self.cfg = load(paths.config_file())
            self.config_error = ""
        except ConfigError as exc:
            self.cfg = None
            self.config_error = str(exc)

    def refresh_header(self) -> None:
        try:
            subtitle = self.screen.query_one("#subtitle", Static)
        except Exception:
            return
        if self.cfg is None:
            subtitle.update(
                f"[red]No usable config.[/red] {self.config_error}\n"
                "Run [b]studip init[/b] in your terminal."
            )
            return
        with State(paths.state_db()) as state:
            last = state.last_run()
        profile = f"profile [b]{paths.profile()}[/b] · " if paths.profile() else ""
        when = last["finished_at"].replace("T", " ")[:16] if last else "never"
        active = sum(1 for c in self.cfg.courses.values() if not c.exclude)
        subtitle.update(
            f"v{__version__} · {profile}{self.cfg.username} @ "
            f"{self.cfg.base_url.split('//')[-1]} · {active} courses · "
            f"last sync: {when}"
        )

    def make_client(self) -> StudipClient:
        assert self.cfg is not None
        password = auth.get_password(self.cfg.base_url, self.cfg.username)
        return StudipClient(self.cfg.base_url, self.cfg.username, password)

    def open_view(self, key: str) -> None:
        if self.cfg is None:
            self.notify("No usable config - run `studip init` first.", severity="error")
            return
        if key in NEEDS_LOGIN and not auth.has_password(self.cfg.base_url, self.cfg.username):
            self.ask_password(lambda: self.push_screen(VIEWS[key]()))
            return
        self.push_screen(VIEWS[key]())

    def ask_password(self, then, error: str = "") -> None:
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
            then()

        self.push_screen(PasswordScreen(cfg, error), got)


def run_tui() -> None:
    StudipRouterApp().run()
