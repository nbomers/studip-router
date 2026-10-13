"""Full-screen views opened from the home screen."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
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
from ..api import ApiError, StudipClient
from ..config import Config, ConfigError
from ..config_edit import set_course
from ..models import RemoteFile
from ..news import News, fetch_news, unread
from ..planner import Action, Kind, build_router
from ..rename import RenameError
from ..reorganize import Move, apply_reorganize, apply_undo, plan_reorganize, plan_undo
from ..report import STYLE, SYMBOL, summarize
from ..state import State
from ..suggest import Suggestion, suggest_rules
from ..sync import (
    CourseEntry,
    CourseStatus,
    SyncPlan,
    build_plan,
    execute_plan,
    list_courses,
    resolve_semester,
    run_merges,
)
from ..tomlwrite import append_blocks, rule_block
from ..wizard import local_course_folders, propose_target
from .widgets import ChoiceScreen, ConfirmScreen


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


class View(Screen):
    """Full-screen view: title bar, Esc/q back, j/k move."""

    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
    ]
    TITLE = ""
    HINT = ""

    def compose_header(self) -> ComposeResult:
        hint = f"  [dim]{self.HINT}[/dim]" if self.HINT else ""
        yield Static(f" [b]{self.TITLE}[/b]{hint}", classes="view-title")

    @property
    def cfg(self) -> Config:
        return self.app.cfg  # type: ignore[attr-defined]

    def client(self) -> StudipClient:
        return self.app.make_client()  # type: ignore[attr-defined]

    def action_cursor(self, direction: str) -> None:
        focused = self.focused
        method = getattr(focused, f"action_cursor_{direction}", None)
        if method:
            method()

    def status(self, text: str, widget_id: str = "status") -> None:
        self.query_one(f"#{widget_id}", Static).update(text)


# -- sync -------------------------------------------------------------------------------


def plan_summary(plan: SyncPlan) -> str:
    counts = summarize(plan)
    parts = []
    for key, label in (
        ("download", "new"),
        ("new-version", "new version(s)"),
        ("overwrite", "updated"),
        ("adopt", "already on disk (recorded)"),
        ("mkdir", "folder(s) to create"),
        ("error", "problem(s)"),
    ):
        if counts.get(key):
            parts.append(f"{counts[key]} {label}")
    default = counts.get("default-routed", 0)
    if default:
        parts.append(f"{default} via default folder")
    return " · ".join(parts) or "nothing to do"


class SyncView(View):
    """Preview table; with ``auto`` it asks to sync right away (home: "s")."""

    TITLE = "Sync"
    BINDINGS: ClassVar = [
        Binding("escape,q", "back", "Back"),
        Binding("s,enter", "start", "Sync now"),
        Binding("v", "toggle_skipped", "Show skipped"),
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

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Fetching course data from Stud.IP…", id="status", classes="status")
        yield DataTable(id="plan", cursor_type="row", zebra_stripes=True)
        yield ProgressBar(id="progress", show_eta=False, show_percentage=False)
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(" ", "Course", "Stud.IP", "→ Local", "Note")
        self.query_one(ProgressBar).display = False
        self.query_one(RichLog).display = False
        self.load_plan()

    @work(thread=True, exclusive=True)
    def load_plan(self) -> None:
        try:
            with self.client() as client, State(paths.state_db()) as state:
                plan = build_plan(client, self.cfg, state)
        except ApiError as exc:
            self.app.call_from_thread(self.status, f"[red]✗ {exc}[/red]")
            return
        self.app.call_from_thread(self.show_plan, plan)

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
                local = rel(a.dest, course_plan.course_dir) if a.dest else ""
                note = a.reason or a.note
                if a.route and a.route.by_default and a.kind is Kind.DOWNLOAD:
                    note = (note + "; " if note else "") + "default folder"
                elif a.route and a.route.rule_id and a.kind is not Kind.SKIP:
                    note = (note + "; " if note else "") + f"rule {a.route.rule_id}"
                if a.unzips:
                    note += "; + unzip"
                table.add_row(
                    Text(SYMBOL[a.kind], style=STYLE[a.kind]),
                    name,
                    remote,
                    local,
                    Text(note, style="dim"),
                )
        todo = self._todo()
        sem = plan.semester.title if plan.semester else "all semesters"
        extra = ""
        if plan.unconfigured:
            extra = (
                f"\n[yellow]{len(plan.unconfigured)} course(s) not set up yet[/yellow] "
                "[dim]→ Courses (c on the home screen)[/dim]"
            )
        if todo:
            self.status(
                f"{sem} · {plan_summary(plan)}\n[b]s[/b] or [b]Enter[/b] syncs, "
                f"[b]v[/b] shows skipped files{extra}"
            )
        else:
            self.status(f"[green]✓ Everything is up to date.[/green] {sem}{extra}")
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

    def action_toggle_skipped(self) -> None:
        self.show_skipped = not self.show_skipped
        if self.plan:
            self.show_plan(self.plan)

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
        self.app.push_screen(
            ConfirmScreen("Sync now?", plan_summary(self.plan)),
            lambda ok: self.run_sync() if ok else None,
        )

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
        except KeyboardInterrupt:
            self.app.call_from_thread(
                self.status,
                "[yellow]Stopped.[/yellow] Finished files are recorded; "
                "the rest follows with the next sync.",
            )
            self.running = False
            return
        except ApiError as exc:
            self.app.call_from_thread(self.status, f"[red]✗ {exc}[/red]")
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
        self.status(f"Syncing {total} change(s)…  [dim]Esc stops after the current file[/dim]")

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
            f"[b]Done.[/b] {new} downloaded, {len(result.outcome.failed)} failed"
            + (
                f", {len(result.outcome.extracted)} archive(s) extracted"
                if result.outcome.extracted
                else ""
            )
            + "."
        )
        for action, msg in result.outcome.failed:
            log.write(f"[red]✗ {action.file.name if action.file else ''}: {msg}[/red]")
        for m in result.merges:
            if m.written:
                log.write(f"[green]✓[/green] merged slides: {m.output}")
        failed = len(result.outcome.failed)
        self.status(
            "[green]✓ Sync finished.[/green]"
            if not failed
            else f"[yellow]Sync finished with {failed} problem(s)[/yellow] - they are "
            "retried next time."
        )
        self.app.refresh_home()  # type: ignore[attr-defined]


# -- courses ----------------------------------------------------------------------------


class CoursesView(View):
    TITLE = "Courses"
    HINT = "Space: sync on/off · Enter: choose folder"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("space", "toggle", "On/off"),
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
        self.query_one(DataTable).add_columns("Sync", "Course", "Semester", "Folder")
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        try:
            with self.client() as client:
                sem = resolve_semester(client, "all" if self.all_semesters else "current")
                entries, missing = list_courses(client, self.cfg, sem)
        except ApiError as exc:
            self.app.call_from_thread(self.status, f"[red]{exc}[/red]")
            return
        label = sem.title if sem else "all semesters"
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
            table.add_row(marks[e.status], e.course.title, e.course.semester_title, folder)
        if entries:
            table.move_cursor(row=min(cursor, len(entries) - 1))
        note = f"{label} · {len(entries)} courses"
        if missing:
            note += f" · [yellow]{missing} configured course(s) not found on Stud.IP[/yellow]"
        self.status(note + "  [dim]a: switch current/all semesters[/dim]")

    def _selected(self) -> CourseEntry | None:
        table = self.query_one(DataTable)
        if not self.entries or table.cursor_row >= len(self.entries):
            return None
        return self.entries[table.cursor_row]

    def _save(self, course_id: str, **fields) -> bool:
        try:
            self.app.cfg = set_course(self.cfg.path, course_id, **fields)  # type: ignore[attr-defined]
        except (ConfigError, OSError) as exc:
            self.notify(f"Not saved: {exc}", severity="error")
            return False
        for e in self.entries:
            e.config = self.app.cfg.courses.get(e.course.id)  # type: ignore[attr-defined]
        self.fill(self.entries)
        self.app.refresh_home()  # type: ignore[attr-defined]
        return True

    def action_toggle(self) -> None:
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
            (f, f"{f}  [dim](suggested)[/dim]" if f == proposal else f) for f in ordered
        ]
        choices.append((EXCLUDE, "[dim]don't sync this course[/dim]"))

        def chosen(folder: str | None) -> None:
            if folder is None:  # cancelled
                return
            if folder == EXCLUDE:
                self._save(entry.course.id, name=entry.course.title, exclude=True)
            else:
                self._save(entry.course.id, name=entry.course.title, target=folder, exclude=None)

        self.app.push_screen(
            ChoiceScreen(f"Folder for “{entry.course.title}”", choices, f"inside {self.cfg.root}"),
            chosen,
        )

    def action_toggle_all_semesters(self) -> None:
        self.all_semesters = not self.all_semesters
        self.status("Loading…")
        self.load()


EXCLUDE = "\0exclude"  # cannot be a folder name


# -- history ----------------------------------------------------------------------------


class HistoryView(View):
    TITLE = "What's new"
    HINT = "Enter/o open · r reveal · Space preview"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("o", "open", "Open"),
        Binding("r", "reveal", "Reveal"),
        Binding("space", "preview", "Preview"),
    ]

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield DataTable(id="history", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("When", "File", "Folder")
        with State(paths.state_db()) as state:
            records = state.recent_files(300)
        for r in records:
            p = Path(r.local_path)
            when = r.synced_at.replace("T", " ")[:16]
            table.add_row(
                when, p.name, Text(rel(p.parent, self.cfg.root), style="dim"), key=r.local_path
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
            self.notify("The file is no longer there (moved or deleted).", severity="warning")
            return
        subprocess.Popen([*args, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @on(DataTable.RowSelected)
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
        else:
            self.action_open()


# -- news -------------------------------------------------------------------------------


class NewsView(View):
    TITLE = "Announcements"
    HINT = "a: show read ones too"
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
            options.add_option(Option(f"{when} {n.course[:20]}: {n.title}"))
        self.query_one("#news-text", Static).update("No unread announcements." if not items else "")
        if items:
            options.highlighted = 0

    @on(OptionList.OptionHighlighted)
    def show(self, event: OptionList.OptionHighlighted) -> None:
        if not self.items or event.option_index >= len(self.items):
            return
        n = self.items[event.option_index]
        when = n.published.strftime("%d.%m.%Y %H:%M") if n.published else ""
        self.query_one("#news-text", Static).update(
            f"[b]{n.title}[/b]\n[dim]{n.course} · {when}[/dim]\n\n{n.text}"
        )
        with State(paths.state_db()) as state:
            state.mark_news_seen([(n.id, n.course_id)])
        self.app.refresh_home()  # type: ignore[attr-defined]

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
            lines.append(f"[b]{c.name or c.target}[/b]  [dim]→ {c.target}[/dim]")
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
                rename = f"  [dim]rename {r.rename}[/dim]" if r.rename else ""
                lines.append(f"  {i}. {r.id}: {what} → {target}{rename}")
            lines.append(f"  [dim]otherwise → {self.cfg.course_default_target(c)}[/dim]\n")
        lines.append(
            "[dim]Edit rules in the config file (Settings → e). Syntax: config.example.toml[/dim]"
        )
        self.query_one("#rule-list", Static).update("\n".join(lines))

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
            self.app.call_from_thread(self.status, f"[red]{exc}[/red]")
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
                    f"{names[cid]}: {what} → {s.rule['target']}  [dim]({s.files} files)[/dim]",
                    i,
                    True,
                )
            )
        self.status(
            f"{len(found)} suggestion(s). Space toggles, [b]w[/b] adds the selected "
            "ones to your config."
            if found
            else "[green]✓ No new suggestions[/green] - your rules cover the Stud.IP folders."
        )

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
                self.notify(f"Could not write: {exc}", severity="error")
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
            result.update("[dim]Type a course and a file name.[/dim]")
            return
        hits = [
            c
            for c in self.cfg.courses.values()
            if not c.exclude and (query in c.name.casefold() or query in c.target.casefold())
        ]
        if len(hits) != 1:
            result.update(
                f"[yellow]{len(hits)} courses match - type more of the name.[/yellow]"
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
            result.update(f"[red]{exc}[/red]")
            return
        if route.skip:
            result.update(f"Rule [b]{route.rule_id}[/b]: not downloaded (skip)")
            return
        where = f"{course.target}/{route.subdir}/{route.name}"
        rule = route.rule_id or "no rule matches → default folder"
        result.update(f"[b]{rule}[/b]\n→ {where}")


# -- reorganize / merge / doctor --------------------------------------------------------


class ReorganizeView(View):
    TITLE = "Reorganize"
    HINT = "move synced files to where the current rules want them"
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
        self.query_one(DataTable).add_columns("From", "To", "Note")
        self.reload()

    def reload(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        with State(paths.state_db()) as state:
            self.moves = plan_reorganize(self.cfg, state)
        for m in self.moves:
            table.add_row(
                rel(m.src, self.cfg.root),
                rel(m.dst, self.cfg.root),
                Text(m.problem, style="yellow"),
            )
        ok = sum(1 for m in self.moves if not m.problem)
        self.status(
            f"{ok} file(s) can be moved. [b]Enter[/b] moves them, [b]u[/b] undoes the "
            "last reorganisation."
            if ok
            else "[green]✓ Everything is where the rules want it.[/green]  "
            "[dim]u: undo the last reorganisation[/dim]"
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
    TITLE = "Merge slides"

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield RichLog(id="merge-log", markup=True, wrap=True)
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
                "No course has a merge setting yet. Add to a course in the config:\n\n"
                '  merge = { source = "Slides", output = "All slides.pdf" }\n\n'
                "[dim]source: folder with the lecture PDFs (relative to the course folder)[/dim]",
            )
        for m in results:
            mark = "[green]✓[/green]" if m.written else "[dim]◎[/dim]"
            self.app.call_from_thread(log.write, f"{mark} {m.course}: {m.reason}")


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
