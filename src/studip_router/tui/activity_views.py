"""HistoryView, DatesView, NewsView screens."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    DataTable,
    Footer,
    OptionList,
    RichLog,
    Static,
)
from textual.widgets.option_list import Option

from .. import paths
from ..api import ApiError
from ..dates import check_dates, upcoming_exams
from ..news import News, fetch_news, unread
from ..report import esc
from ..state import State
from .common import DOCUMENTS, RUNNABLE, View, clip, plural, rel
from .widgets import (
    ConfirmScreen,
    course_color,
)


class HistoryView(View):
    TITLE = "What's new"
    HINT = "Enter open · f show in folder · Space preview"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("f", "reveal", "Show in folder"),
        # before 0.4.2; o and r are Reorganize and Rules on the home screen
        Binding("o", "open", show=False),
        Binding("r", "reveal", show=False),
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
        try:
            subprocess.Popen(
                [*args, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError as exc:
            self.notify(f"Could not open the file: {esc(exc)}", severity="error")

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
        if path is not None and path.suffix.lower() not in DOCUMENTS:
            self.app.push_screen(
                ConfirmScreen(
                    f"Open {esc(path.name)} with its default application?",
                    "This type is not a supported document. Its application may run code. "
                    "Only open it if you trust the file. You can reveal it in its folder instead.",
                ),
                lambda ok: (
                    self._run("open" if sys.platform == "darwin" else "xdg-open") if ok else None
                ),
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


class NewsView(View):
    TITLE = "Announcements"
    HINT = "a show read ones too · x mark all read"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("a", "toggle_all", "Show all"),
        Binding("x", "read_all", "All read"),
        Binding("v", "pane", "List / text"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.items: list[News] = []
        self.show_all = False
        self.marked = False  # something was marked read: home recounts once, on leaving
        self.current: int | None = None  # index shown right now
        self.moved = False  # the user moved through the list

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        with Horizontal():
            yield OptionList(id="news-list")
            with VerticalScroll(id="news-body"):
                yield Static("Loading…", id="news-text")
        yield Footer()

    def on_mount(self) -> None:
        self._body_open = False
        self._layout_news()
        self.query_one(OptionList).focus()
        self.load()

    def on_resize(self) -> None:
        self._layout_news()

    def _layout_news(self) -> None:
        narrow = self.size.width < 70
        self.set_class(narrow, "news-narrow")
        self.query_one("#news-list").display = not narrow or not getattr(self, "_body_open", False)
        self.query_one("#news-body").display = not narrow or getattr(self, "_body_open", False)

    def action_pane(self) -> None:
        self._body_open = not self._body_open
        self._layout_news()
        self.query_one("#news-body" if self._body_open else "#news-list").focus()

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
        self.current = None  # a new list: nothing shown yet
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
        # Read = looked at and moved on (or Enter): the one shown when the screen
        # opens is not read just because it was first in the list.
        if self.current is not None and self.current != event.option_index:
            self._mark(self.current)
            self.moved = True
        self.current = event.option_index

    @on(OptionList.OptionSelected)
    def read(self, event: OptionList.OptionSelected) -> None:
        self._mark(event.option_index)

    def _mark(self, index: int) -> None:
        if index >= len(self.items):
            return
        n = self.items[index]
        with State(paths.state_db()) as state:
            state.mark_news_seen([(n.id, n.course_id)])
        # The home screen recounts once, on leaving: not a full fetch per cursor move.
        self.marked = True

    def on_unmount(self) -> None:
        if self.moved and self.current is not None:
            self._mark(self.current)
        if self.marked:
            with contextlib.suppress(Exception):  # the app may be closing
                self.app.refresh_home(remote_changed=True)  # type: ignore[attr-defined]

    def action_read_all(self) -> None:
        if not self.items:
            return
        with State(paths.state_db()) as state:
            state.mark_news_seen([(n.id, n.course_id) for n in self.items])
        self.marked = True
        self.notify(f"{len(self.items)} announcement(s) marked as read.")
        if not self.show_all:
            self.load()

    def action_toggle_all(self) -> None:
        self.show_all = not self.show_all
        self.load()
