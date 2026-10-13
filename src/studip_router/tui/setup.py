"""First-run setup inside the TUI (the same as `studip init`)."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, SelectionList, Static

from .. import auth, paths
from ..api import ApiError, AuthFailed, StudipClient
from ..fsutil import sanitize_filename
from ..models import Course
from ..wizard import (
    CourseChoice,
    detect_default_target,
    local_course_folders,
    propose_target,
    render_config,
)
from .widgets import ConfirmScreen


class SetupScreen(Screen):
    BINDINGS: ClassVar = [Binding("escape", "app.quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.courses: list[Course] = []
        self.targets: list[str | None] = []
        self.semester = ""

    def compose(self) -> ComposeResult:
        yield Static(
            " [b]Welcome to studip-router[/b]  [dim]set up in a minute[/dim]", classes="view-title"
        )
        with VerticalScroll(id="setup"):
            with Vertical(id="step1", classes="form"):
                yield Label("Stud.IP address of your university")
                yield Input(placeholder="https://studip.example.edu", id="url")
                yield Label("Username")
                yield Input(id="username")
                yield Label("Password  [dim](kept in your OS keychain only)[/dim]")
                yield Input(password=True, id="password")
                yield Label("Folder that contains your course folders")
                yield Input(str(Path("~/Uni").expanduser()), id="root")
                yield Button("Connect", variant="primary", id="connect")
                yield Static(id="status", classes="status")
            with Vertical(id="step2", classes="form"):
                yield Static(id="courses-title")
                yield SelectionList[int](id="course-select")
                yield Label("Folder (inside each course folder) for files no rule matches")
                yield Input(id="default-target")
                with Horizontal(classes="buttons"):
                    yield Button("Save", variant="primary", id="save")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#step2").display = False
        self.query_one("#url", Input).focus()

    @on(Input.Submitted)
    def next_field(self, event: Input.Submitted) -> None:
        if event.input.id == "root":
            self.connect()
        elif event.input.id != "default-target":
            self.focus_next()

    @on(Button.Pressed, "#connect")
    def connect(self) -> None:
        values = {
            k: self.query_one(f"#{k}", Input).value.strip()
            for k in ("url", "username", "password", "root")
        }
        if not all(values.values()):
            self.query_one("#status", Static).update("[yellow]Please fill in all fields.[/yellow]")
            return
        if not values["url"].startswith(("https://", "http://")):
            values["url"] = "https://" + values["url"]
        if not Path(values["root"]).expanduser().is_dir():
            self.query_one("#status", Static).update(
                f"[yellow]{values['root']} does not exist.[/yellow]"
            )
            return
        self.query_one("#status", Static).update("Connecting…")
        self._connect(values)

    @work(thread=True, exclusive=True)
    def _connect(self, values: dict[str, str]) -> None:
        try:
            with StudipClient(
                values["url"].rstrip("/"), values["username"], values["password"]
            ) as client:
                me = client.me()
                sem = client.current_semester()
                courses = [c for c in client.courses(me["id"]) if c.runs_in(sem)]
        except AuthFailed:
            self.app.call_from_thread(
                self.query_one("#status", Static).update, "[red]Wrong username or password.[/red]"
            )
            return
        except ApiError as exc:
            self.app.call_from_thread(
                self.query_one("#status", Static).update,
                f"[red]{exc}[/red]\n[dim]Is the JSON:API enabled at your university? "
                "Try the address in a browser: …/jsonapi.php/v1/discovery[/dim]",
            )
            return
        self.values = values
        self.semester = sem.title
        self.app.call_from_thread(self.show_courses, sorted(courses, key=lambda c: c.title))

    def show_courses(self, courses: list[Course]) -> None:
        self.courses = courses
        root = Path(self.values["root"]).expanduser()
        folders = local_course_folders(root)
        self.targets = [propose_target(c.title, folders) for c in courses]
        selection = self.query_one(SelectionList)
        for i, (course, target) in enumerate(zip(courses, self.targets, strict=True)):
            where = f"→ {target}" if target else "[yellow]→ new folder[/yellow]"
            selection.add_option((f"{course.title}  [dim]{where}[/dim]", i, target is not None))
        self.query_one("#courses-title", Static).update(
            f"[green]✓ Connected.[/green] {len(courses)} courses in {self.semester}. "
            "Space toggles which ones to sync."
        )
        dirs = [root / t for t in self.targets if t]
        self.query_one("#default-target", Input).value = detect_default_target(dirs) or "_unsorted"
        self.query_one("#step1").display = False
        self.query_one("#step2").display = True
        selection.focus()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        root = Path(self.values["root"]).expanduser()
        selected = set(self.query_one(SelectionList).selected)
        choices = []
        to_create: list[Path] = []
        for i, course in enumerate(self.courses):
            target = self.targets[i]
            if i in selected and target is None:
                target = sanitize_filename(course.title)
                to_create.append(root / target)
            choices.append(
                CourseChoice(
                    course.id,
                    course.title,
                    course.semester_title,
                    target if i in selected else None,
                )
            )
        default_target = self.query_one("#default-target", Input).value.strip() or "_unsorted"

        def write(ok: bool | None = True) -> None:
            if not ok:
                return
            for folder in to_create:
                folder.mkdir(parents=True, exist_ok=True)
            path = paths.config_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render_config(
                    self.values["url"].rstrip("/"),
                    self.values["username"],
                    self.values["root"],
                    choices,
                    default_target,
                ),
                encoding="utf-8",
            )
            auth.set_password(
                self.values["url"].rstrip("/"), self.values["username"], self.values["password"]
            )
            self.app.finish_setup()  # type: ignore[attr-defined]

        if to_create:
            names = ", ".join(p.name for p in to_create)
            self.app.push_screen(
                ConfirmScreen(f"Create {len(to_create)} course folder(s)?", names), write
            )
        else:
            write()
