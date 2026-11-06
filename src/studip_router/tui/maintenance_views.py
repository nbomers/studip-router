"""ReorganizeView, MergeView, DoctorView screens."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import (
    DataTable,
    Footer,
    RichLog,
    Static,
)

from .. import doctor, paths
from ..config import ConfigError
from ..config_edit import set_course
from ..reorganize import (
    Move,
    apply_reorganize,
    apply_undo,
    plan_reorganize,
    plan_undo,
)
from ..report import esc
from ..state import State
from ..suggest import (
    local_category_dirs,
    local_subdirs,
)
from ..sync import (
    MergeOutcome,
    run_merges,
)
from .common import NO_MERGE, View, clip, plural, rel, short_path
from .widgets import (
    ChoiceScreen,
    ConfirmScreen,
    course_color,
)


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
        self.fill()  # merging writes files: only on "m" (and after each sync)

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
