"""CoursesView, SemesterView screens."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import (
    DataTable,
    Footer,
    Static,
)

from .. import semester
from ..api import ApiError
from ..config import ConfigError, CourseConfig, unsafe_folder
from ..config_edit import remove_course, set_course
from ..report import esc
from ..sync import (
    CourseEntry,
    CourseStatus,
    list_courses,
    resolve_semester,
)
from ..wizard import (
    course_folder_name,
    create_course_folder,
    display_path,
    local_course_folders,
    propose_target,
)
from .common import EXCLUDE, NEW_FOLDER, View, clip, plural
from .widgets import (
    ChoiceScreen,
    ConfirmScreen,
    TextScreen,
    course_color,
)


class CoursesView(View):
    TITLE = "Courses"
    HINT = "Space on/off · Enter folder · a all semesters · x remove a course gone from Stud.IP"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("space", "toggle_course", "On/off"),
        Binding("enter", "choose_folder", "Folder"),
        Binding("a", "toggle_all_semesters", "All semesters"),
        Binding("x", "remove_course", "Remove"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[CourseEntry] = []
        self.missing: list[CourseConfig] = []  # in the config, no longer on Stud.IP
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
        self.app.call_from_thread(self.fill, entries, label, missing)

    def fill(
        self,
        entries: list[CourseEntry],
        label: str = "",
        missing: list[CourseConfig] | None = None,
    ) -> None:
        self.entries = entries
        if missing is not None:
            self.missing = missing
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
        for c in self.missing:
            table.add_row(
                Text("✗ gone", style="red"),
                Text(clip(c.name or c.target or c.id, 48), style="dim"),
                Text("not on Stud.IP", style="dim"),
                Text("x: remove from config", style="yellow dim"),
            )
        rows = len(entries) + len(self.missing)
        if rows:
            table.move_cursor(row=min(cursor, rows - 1))
        on = sum(1 for e in entries if e.status is CourseStatus.ACTIVE)
        new = sum(1 for e in entries if e.status is CourseStatus.UNCONFIGURED)
        note = f"{plural(on, 'course')} synced [dim]of {len(entries)} · {esc(label)}[/dim]"
        if new:
            note += f'\n[yellow][b]{new} new[/b][/yellow] - Enter: choose a folder or "don\'t sync"'
        if self.missing:
            note += (
                f"\n[yellow][b]{len(self.missing)}[/b] set-up course(s) no longer on Stud.IP"
                "[/yellow] [dim](deleted, or you left them) - x removes one from the config,"
                " your files stay[/dim]"
            )
        self.status(note)

    def _selected_missing(self) -> CourseConfig | None:
        index = self.query_one(DataTable).cursor_row - len(self.entries)
        return self.missing[index] if 0 <= index < len(self.missing) else None

    def action_remove_course(self) -> None:
        gone = self._selected_missing()
        if gone is None:
            self.notify(
                "Only courses marked ✗ gone can be removed; Space turns a course off.",
                severity="warning",
            )
            return
        label = gone.name or gone.target or gone.id
        rules = f" and its {plural(len(gone.rules), 'rule')}" if gone.rules else ""

        def confirmed(ok: bool | None) -> None:
            if not ok:
                return
            try:
                self.app.cfg = remove_course(self.cfg.path, gone.id)  # type: ignore[attr-defined]
            except (ConfigError, OSError, KeyError) as exc:
                self.notify(f"Not removed: {esc(exc)}", severity="error")
                return
            self.missing = [c for c in self.missing if c.id != gone.id]
            self.fill(self.entries)
            self.app.refresh_home()  # type: ignore[attr-defined]
            self.notify(f"Removed {esc(label)} from the config.")

        self.app.push_screen(
            ConfirmScreen(
                f"Remove “{esc(label)}”{rules} from the config?",
                "Files already synced stay where they are. A backup of the config is kept"
                " as config.toml.bak.",
            ),
            confirmed,
        )

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
        if self._selected_missing() is not None:  # no folder to choose: offer removal
            self.action_remove_course()
            return
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


class SemesterView(View):
    """Semester change: a folder for the new semester, new courses, archive old ones."""

    TITLE = "New semester"
    HINT = "Space changes the selected line · Enter applies everything"
    BINDINGS: ClassVar = [
        *View.BINDINGS,
        Binding("space", "change_line", "Change"),
        Binding("enter", "apply", "Apply"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.change: semester.SemesterChange | None = None
        self.choices: semester.Choices | None = None
        self.proposal: Path | None = None  # the root folder proposed at first
        self.rows: list[tuple[str, str]] = []  # (kind, id) per table row
        self.done = False

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Loading…", id="status", classes="status")
        yield DataTable(id="semester", cursor_type="row", zebra_stripes=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(" ", "Course", "What happens")
        self.load()

    @work(thread=True, exclusive=True)
    def load(self) -> None:
        self.busy(text="Asking Stud.IP for your courses and semesters…")
        try:
            with self.client() as client:
                change = semester.detect(client, self.cfg)
        except ApiError as exc:
            self.idle()
            self.check_login(exc)
            self.app.call_from_thread(self.status, f"[red]{esc(exc)}[/red]")
            return
        self.idle()
        self.app.call_from_thread(self.show, change)

    def after_login(self) -> None:
        self.load()

    def show(self, change: semester.SemesterChange | None) -> None:
        self.change = change
        if change is None:
            self.status(
                "[green]✓ No semester change to make.[/green] [dim]No set-up course has ended"
                " and no new semester has started. New courses mid-semester: Courses (c).[/dim]"
            )
            return
        self.proposal = change.proposed_root
        self.choices = semester.default_choices(change)
        self.render_rows()

    def _root(self) -> Path:
        assert self.change is not None and self.choices is not None
        return self.choices.new_root or self.change.root

    def render_rows(self) -> None:
        change, choices = self.change, self.choices
        if change is None or choices is None:
            return
        home = Path.home()
        table = self.query_one(DataTable)
        cursor = table.cursor_row
        table.clear()
        self.rows = []
        if change.new_courses:
            new_root = choices.new_root is not None and choices.new_root != change.root
            table.add_row(
                Text("▸ new" if new_root else "▸ keep", style="green" if new_root else "dim"),
                Text("Folder for the new courses", style="bold"),
                Text(
                    display_path(self._root(), home)
                    + ("  (old courses keep their folders)" if new_root else "")
                ),
            )
            self.rows.append(("root", ""))
        for course in change.new_courses:
            added = course.id in choices.add
            table.add_row(
                Text("+ add" if added else "- skip", style="green" if added else "dim"),
                Text(clip(course.title, 48), style="bold" if added else "dim"),
                Text(
                    f"→ {clip(choices.add[course.id], 30)}/ ({', '.join(change.subfolders)})"
                    if added
                    else "not synced",
                    style="" if added else "dim",
                ),
            )
            self.rows.append(("new", course.id))
        for old in change.old_courses:
            archived = old.config.id in choices.archive
            until = old.course.end_semester.title if old.course.end_semester else "?"
            table.add_row(
                Text(
                    "◼ archive" if archived else "● keep", style="yellow" if archived else "green"
                ),
                Text(clip(old.label, 48), style="dim" if archived else course_color(old.config.id)),
                Text(
                    ("no longer synced, files stay" if archived else "keeps syncing")
                    + (f" · ended {until}" if old.ended else f" · runs until the end of {until}"),
                    style="dim",
                ),
            )
            self.rows.append(("old", old.config.id))
        if self.rows:
            table.move_cursor(row=min(cursor, len(self.rows) - 1))
        lines = [f"[b]{esc(change.semester.title)}[/b]"]
        if change.new_courses:
            lines.append(f"{plural(len(choices.add), 'new course')} to set up")
        if change.old_courses:
            lines.append(f"{plural(len(choices.archive), 'course')} to archive")
        running = [o for o in change.old_courses if not o.ended]
        note = (
            "\n[dim]Courses that have not ended yet keep syncing unless you archive them"
            " (exams and late solutions still come).[/dim]"
            if running
            else ""
        )
        self.status(" · ".join(lines) + "  → [b]Enter[/b] applies" + note)

    def _selected(self) -> tuple[str, str] | None:
        row = self.query_one(DataTable).cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def _retarget(self) -> None:
        """New folder names for the courses being added, below the chosen root."""
        assert self.change is not None and self.choices is not None
        self.change.proposed_root = self.choices.new_root
        targets = semester.new_targets(self.change, new_root=self.choices.new_root is not None)
        self.choices.add = {cid: targets[cid] for cid in self.choices.add}

    def action_change_line(self) -> None:
        selected = self._selected()
        if self.done or selected is None or self.choices is None or self.change is None:
            return
        kind, cid = selected
        choices = self.choices
        if kind == "new":
            if cid in choices.add:
                del choices.add[cid]
                choices.skip.add(cid)
            else:
                choices.skip.discard(cid)
                choices.add[cid] = semester.new_targets(
                    self.change, new_root=choices.new_root is not None
                )[cid]
        elif kind == "old":
            if cid in choices.archive:
                choices.archive.discard(cid)
                choices.keep.add(cid)
            else:
                choices.archive.add(cid)
                choices.keep.discard(cid)
        elif kind == "root":
            self._choose_root()
            return
        self.render_rows()

    def _choose_root(self) -> None:
        assert self.change is not None and self.choices is not None
        home = Path.home()
        options: list[tuple[str, str]] = []
        if self.proposal is not None:
            options.append(
                ("proposal", f"{esc(display_path(self.proposal, home))}  [dim](new folder)[/dim]")
            )
        options.append(
            ("keep", f"{esc(display_path(self.change.root, home))}  [dim](as now)[/dim]")
        )
        options.append(("other", "another folder…"))

        def use(root: Path | None) -> None:
            assert self.choices is not None
            self.choices.new_root = root
            self._retarget()
            self.render_rows()

        def typed(value: str | None) -> None:
            if not value:
                return
            path = semester.typed_root(self.cfg, value)
            if problem := unsafe_folder(str(path)):
                self.notify(f"{esc(value)}: {esc(problem)}", severity="error")
                return
            use(path)

        def chosen(choice: str | None) -> None:
            if choice == "proposal":
                use(self.proposal)
            elif choice == "keep":
                use(None)
            elif choice == "other":
                start = self.choices.new_root if self.choices and self.choices.new_root else None
                self.app.push_screen(
                    TextScreen(
                        "Folder for the new semester",
                        display_path(start or self.change.root, home),  # type: ignore[union-attr]
                        "Old courses keep their folders.",
                    ),
                    typed,
                )

        self.app.push_screen(ChoiceScreen("Folder for the new courses", options), chosen)

    @on(DataTable.RowSelected)
    def _row_selected(self) -> None:
        self.action_apply()

    def action_apply(self) -> None:
        change, choices = self.change, self.choices
        if self.done or change is None or choices is None:
            return
        parts = []
        if choices.new_root and choices.new_root != change.root:
            parts.append(f"create {esc(display_path(choices.new_root, Path.home()))}")
        if choices.add:
            parts.append(f"set up {plural(len(choices.add), 'new course')}")
        if choices.archive:
            parts.append(f"archive {plural(len(choices.archive), 'course')}")
        if not parts and not choices.skip and not choices.keep:
            self.notify("Nothing to change.")
            return

        def confirmed(ok: bool | None) -> None:
            if not ok:
                return
            try:
                applied = semester.apply_change(self.cfg, change, choices)
            except (ConfigError, ValueError, OSError) as exc:
                self.notify(f"Nothing changed: {esc(exc)}", severity="error")
                return
            self.app.cfg = applied.config  # type: ignore[attr-defined]
            self.done = True
            self.app.refresh_home(remote_changed=True)  # type: ignore[attr-defined]
            lines = ["[green]✓ Done.[/green] [dim]A backup of the config: config.toml.bak[/dim]"]
            lines += [
                f"[green]+[/green] {esc(display_path(p, Path.home()))}" for p in applied.created
            ]
            lines += [f"[red]✗ {esc(p)}[/red]" for p in applied.problems]
            if choices.add:
                lines.append("Next: Rules (r) suggests rules for the new courses, then sync.")
            self.status("\n".join(lines))
            self.query_one(DataTable).display = False

        self.app.push_screen(
            ConfirmScreen(
                ", ".join(parts).capitalize() + "?" if parts else "Save your choices?",
                "Files already synced stay where they are. Archived courses can be turned on"
                " again under Courses (c).",
            ),
            confirmed,
        )
