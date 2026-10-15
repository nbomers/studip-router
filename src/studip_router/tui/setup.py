"""First-run setup inside the TUI (the same as `studip init`).

Three steps, each prefilled with a good guess: log in; pick the folder with the
course folders (found by looking for folders named like your courses); choose
the courses and the structure new course folders get.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, OptionList, SelectionList, Static
from textual.widgets.option_list import Option

from .. import auth, paths
from ..api import ApiError, AuthFailed, StudipClient, base_url_problem
from ..config import DEFAULT_COURSE_FOLDERS
from ..models import Course
from ..wizard import (
    CourseChoice,
    course_folder_name,
    create_course_folder,
    detect_course_folders,
    detect_default_target,
    display_path,
    find_roots,
    home_path,
    local_course_folders,
    new_root,
    propose_target,
    render_config,
)
from .widgets import ConfirmScreen


def _split(text: str) -> tuple[str, ...]:
    return tuple(p.strip() for p in text.split(",") if p.strip())


class SetupScreen(Screen):
    BINDINGS: ClassVar = [Binding("escape", "app.quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self.courses: list[Course] = []
        self.targets: list[str | None] = []
        self.semester = ""
        self.root_paths: list[Path] = []
        self.home = Path.home()

    def compose(self) -> ComposeResult:
        yield Static(
            " [b]Welcome to studip-router[/b]  [dim]set up in a minute[/dim]", classes="view-title"
        )
        with VerticalScroll(id="setup"):
            with Vertical(id="step1", classes="form"):
                yield Static("[b]1/3[/b]  Log in to Stud.IP")
                yield Label("Stud.IP address of your university")
                yield Input(placeholder="https://studip.example.edu", id="url")
                yield Label("Username")
                yield Input(id="username")
                yield Label("Password  [dim](kept in your OS keychain only)[/dim]")
                yield Input(password=True, id="password")
                yield Button("Connect", variant="primary", id="connect")
                yield Static(id="status", classes="status")
            with Vertical(id="step2", classes="form"):
                yield Static(id="root-title")
                yield OptionList(id="root-choices")
                yield Label("Folder  [dim](created if it does not exist)[/dim]")
                yield Input(id="root")
                yield Button("Next", variant="primary", id="next")
                yield Static(id="root-status", classes="status")
            with Vertical(id="step3", classes="form"):
                yield Static(id="courses-title")
                yield SelectionList[int](id="course-select")
                yield Label("Subfolders of new course folders  [dim](comma separated)[/dim]")
                yield Input(id="course-folders")
                yield Label("Folder for files that no rule and no folder name places")
                yield Input(id="default-target")
                with Horizontal(classes="buttons"):
                    yield Button("Save", variant="primary", id="save")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#step2").display = False
        self.query_one("#step3").display = False
        self.query_one("#url", Input).focus()

    @on(Input.Submitted)
    def next_field(self, event: Input.Submitted) -> None:
        if event.input.id == "password":
            self.connect()
        elif event.input.id == "root":
            self.choose_root()
        elif event.input.id not in ("default-target", "course-folders"):
            self.focus_next()

    # -- step 1: log in -------------------------------------------------------------

    @on(Button.Pressed, "#connect")
    def connect(self) -> None:
        values = {
            k: self.query_one(f"#{k}", Input).value.strip() for k in ("url", "username", "password")
        }
        if not all(values.values()):
            self.query_one("#status", Static).update("[yellow]Please fill in all fields.[/yellow]")
            return
        if not values["url"].startswith(("https://", "http://")):
            values["url"] = "https://" + values["url"]
        if problem := base_url_problem(values["url"]):
            self.query_one("#status", Static).update(f"[yellow]The address {problem}.[/yellow]")
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
        courses = sorted(courses, key=lambda c: c.title)
        self.app.call_from_thread(
            self.query_one("#status", Static).update, "Looking for your course folders…"
        )
        roots = find_roots([c.title for c in courses], self.home)
        self.app.call_from_thread(self.show_roots, courses, roots)

    # -- step 2: where the course folders are ----------------------------------------

    def show_roots(self, courses: list[Course], roots: list) -> None:
        self.courses = courses
        fresh = new_root(self.semester, self.home)
        self.root_paths = [r.path for r in roots]
        options = [Option(r.label(self.home, len(courses))) for r in roots]
        if fresh not in self.root_paths:
            self.root_paths.append(fresh)
            options.append(Option(f"{display_path(fresh, self.home)}  [dim](new)[/dim]"))
        choices = self.query_one("#root-choices", OptionList)
        choices.add_options(options)
        found = bool(roots and roots[0].matches)
        self.query_one("#root-title", Static).update(
            f"[green]✓ Connected.[/green] {len(courses)} courses in {self.semester}.\n\n"
            "[b]2/3[/b]  Where do your course folders live?  "
            + (
                "[dim]Found where your courses already are.[/dim]"
                if found
                else "[dim]No course folders found: start with a new folder.[/dim]"
            )
        )
        self.query_one("#root", Input).value = home_path(self.root_paths[0], self.home)
        self.query_one("#step1").display = False
        self.query_one("#step2").display = True
        choices.highlighted = 0
        choices.focus()

    @on(OptionList.OptionHighlighted, "#root-choices")
    def root_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.query_one("#root", Input).value = home_path(
            self.root_paths[event.option_index], self.home
        )

    @on(OptionList.OptionSelected, "#root-choices")
    def root_selected(self, event: OptionList.OptionSelected) -> None:
        self.query_one("#root", Input).value = home_path(
            self.root_paths[event.option_index], self.home
        )
        self.choose_root()

    @on(Button.Pressed, "#next")
    def choose_root(self) -> None:
        text = self.query_one("#root", Input).value.strip()
        if not text:
            self.query_one("#root-status", Static).update("[yellow]Choose a folder.[/yellow]")
            return
        root = Path(text).expanduser()
        if root.exists() and not root.is_dir():
            self.query_one("#root-status", Static).update(f"[yellow]{text} is a file.[/yellow]")
            return
        self.values["root"] = home_path(root, self.home)
        self.show_courses(root)

    # -- step 3: courses and their folders ------------------------------------------

    def show_courses(self, root: Path) -> None:
        folders = local_course_folders(root)
        self.targets = [propose_target(c.title, folders) for c in self.courses]
        matched = any(self.targets)
        selection = self.query_one(SelectionList)
        selection.clear_options()
        for i, (course, target) in enumerate(zip(self.courses, self.targets, strict=True)):
            where = (
                f"→ {target}"
                if target
                else f"[yellow]→ new: {course_folder_name(course.title)}[/yellow]"
            )
            # with existing folders only those courses; starting fresh: all of them
            selection.add_option(
                (f"{course.title}  [dim]{where}[/dim]", i, bool(target) or not matched)
            )
        self.query_one("#courses-title", Static).update(
            "[b]3/3[/b]  Which courses should be synced?  [dim]Space toggles. New course "
            "folders get the subfolders below, so files land in the right one at once.[/dim]"
        )
        dirs = [root / t for t in self.targets if t]
        template = detect_course_folders(dirs) or DEFAULT_COURSE_FOLDERS
        self.query_one("#course-folders", Input).value = ", ".join(template)
        default = detect_default_target(dirs) or (
            "Sonstiges" if "Sonstiges" in template else template[-1]
        )
        self.query_one("#default-target", Input).value = default
        self.query_one("#step2").display = False
        self.query_one("#step3").display = True
        selection.focus()

    @on(Button.Pressed, "#save")
    def save(self) -> None:
        root = Path(self.values["root"]).expanduser()
        selected = set(self.query_one(SelectionList).selected)
        template = _split(self.query_one("#course-folders", Input).value)
        default_target = self.query_one("#default-target", Input).value.strip() or "Sonstiges"
        choices = []
        to_create: list[str] = []
        for i, course in enumerate(self.courses):
            target = self.targets[i]
            if i in selected and target is None:
                target = course_folder_name(course.title)
                to_create.append(target)
            choices.append(
                CourseChoice(
                    course.id,
                    course.title,
                    course.semester_title,
                    target if i in selected else None,
                )
            )

        def write(ok: bool | None = True) -> None:
            if not ok:
                return
            try:
                root.mkdir(parents=True, exist_ok=True)
                for name in to_create:
                    create_course_folder(root, name, template)
            except OSError as exc:
                self.notify(f"Could not create the folders: {exc}", severity="error")
                return
            path = paths.config_file()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                render_config(
                    self.values["url"].rstrip("/"),
                    self.values["username"],
                    self.values["root"],
                    choices,
                    default_target,
                    template,
                ),
                encoding="utf-8",
            )
            auth.set_password(
                self.values["url"].rstrip("/"), self.values["username"], self.values["password"]
            )
            self.app.finish_setup()  # type: ignore[attr-defined]

        if to_create:
            inside = f"in {display_path(root, self.home)}, each with: {', '.join(template)}"
            self.app.push_screen(
                ConfirmScreen(f"Create {len(to_create)} course folder(s)?", inside), write
            )
        else:
            write()
