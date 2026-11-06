"""SyncView screens."""

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
    Input,
    ProgressBar,
    RichLog,
    Static,
)

from .. import paths
from ..api import ApiError
from ..dates import Change, check_dates
from ..fsutil import Locked
from ..planner import Action, Kind
from ..reorganize import (
    pending_moves,
)
from ..report import STYLE, SYMBOL, esc, reorganize_hint, summarize, unlocked_lines
from ..rules import parse_rules
from ..sorting_report import report_text
from ..state import State
from ..suggest import (
    local_subdirs,
    name_pattern,
    rule_from_file,
)
from ..sync import (
    SyncPlan,
    build_plan,
    execute_plan,
)
from ..tomlwrite import append_blocks, rule_block
from .common import (
    NEW_FOLDER,
    View,
    _names,
    clip,
    is_default_routed,
    plan_summary,
    plan_summary_short,
    rel,
    skipped_reasons,
    why,
)
from .widgets import (
    ChoiceScreen,
    ConfirmScreen,
    DetailScreen,
    ReportScreen,
    TextScreen,
    course_color,
)


class SyncView(View):
    """Preview table; with ``auto`` it asks to sync right away (home: "s")."""

    TITLE = "Sync"
    BINDINGS: ClassVar = [
        Binding("escape,q", "back", "Back"),
        Binding("s,enter", "start", "Sync now"),
        Binding("v", "toggle_skipped", "Show skipped"),
        Binding("i", "ignore", "Never download"),
        Binding("plus", "add_rule", "Rule for this file"),
        Binding("b", "report", "Sorted wrong?"),
        Binding("d", "details", "Details"),
        Binding("slash", "filter", "Filter"),
        Binding("right_square_bracket", "page(1)", "Next page"),
        Binding("left_square_bracket", "page(-1)", "Previous page"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("r", "refresh", show=False),  # before 0.4.2; r is Rules on the home screen
        Binding("j", "cursor('down')", show=False),
        Binding("k", "cursor('up')", show=False),
    ]

    def __init__(self, auto: bool = False) -> None:
        super().__init__()
        self.auto = auto
        self.plan: SyncPlan | None = None
        self.page = 0
        self.page_size = 500
        self.show_skipped = False
        self.running = False
        self.cancel = False
        self.used = False  # the plan was executed (or tried): it is stale now
        self.date_changes: list[Change] = []

    def compose(self) -> ComposeResult:
        yield from self.compose_header()
        yield Static("Fetching course data from Stud.IP…", id="status", classes="status")
        yield Input(
            placeholder="Filter course, source or target · Tab returns to the list",
            id="plan-filter",
        )
        yield DataTable(id="plan", cursor_type="row", zebra_stripes=True)
        yield ProgressBar(id="progress", show_eta=False, show_percentage=False)
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(" ", "Course", "On Stud.IP", "→ Saved as", "Why")
        self.query_one("#progress", ProgressBar).display = False
        self.query_one(RichLog).display = False
        self.query_one("#plan-filter", Input).display = False
        self.query_one(DataTable).focus()
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
                complete = (  # every configured course listed all its files
                    not plan.errors
                    and not plan.closed
                    and not plan.missing
                    and not any(
                        a.kind is Kind.ERROR and a.file is None
                        for p in plan.plans
                        for a in p.actions
                    )
                )
                if complete:  # only then: failures of files Stud.IP no longer lists go
                    state.forget_failures_except(
                        {a.file.id for p in plan.plans for a in p.actions if a.file}
                    )
        except ApiError as exc:
            self.idle()
            self.check_login(exc)
            self.app.call_from_thread(self.status, f"[red]✗ {esc(exc)}[/red]")
            return
        self.idle()
        self.app.call_from_thread(self.show_plan, plan)

    def show_plan(self, plan: SyncPlan) -> None:
        self.plan = plan
        self.used = False
        # Back from a finished or stopped run: the table again, not the old log.
        self.query_one("#progress", ProgressBar).display = False
        self.query_one(RichLog).display = False
        self.query_one(DataTable).display = True
        table = self.query_one(DataTable)
        table.clear()
        self.row_actions: list[Action] = []

        def order(a: Action) -> int:  # problems first, then what needs a look
            if a.kind is Kind.ERROR:
                return 0
            if is_default_routed(a):
                return 1
            return 3 if a.kind is Kind.SKIP else 2

        query = self.query_one("#plan-filter", Input).value.casefold().strip()
        matched = 0
        start = self.page * self.page_size
        for course_plan in plan.plans:
            name = course_plan.course.name or course_plan.course.target
            for a in sorted(course_plan.actions, key=order):
                if a.kind is Kind.SKIP and not self.show_skipped:
                    continue
                remote = f"{a.file.folder_path}/{a.file.name}".lstrip("/") if a.file else ""
                local = rel(a.dest, course_plan.course_dir) if a.dest else ""
                if query and query not in f"{name} {remote} {local}".casefold():
                    continue
                matched += 1
                if not start < matched <= start + self.page_size:
                    continue
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
        self._matched_rows = matched
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
        if plan.missing:
            names = ", ".join(clip(c.name or c.target, 30) for c in plan.missing)
            lines.append(
                f"[yellow]! not on Stud.IP any more: {esc(names)}[/yellow] "
                "[dim](deleted, or you left it - your files stay; Courses (c): x removes it"
                " from the config)[/dim]"
            )
        if plan.unconfigured:
            lines.append(
                f"[yellow][b]{len(plan.unconfigured)}[/b] new course(s) not set up yet[/yellow] "
                "[dim]- c on the home screen[/dim]"
            )
        if matched > self.page_size:
            lines.append(
                f"[dim]Rows {start + 1}-{min(start + self.page_size, matched)} of {matched} "
                "· brackets change page · / filters[/dim]"
            )
        extra = "".join(f"\n{line}" for line in lines)
        if todo:
            self.status(
                f"{plan_summary_short(plan)} [dim]· {esc(sem)}[/dim]  → [b]Enter[/b] syncs{extra}"
            )
        else:
            self.status(f"[green]✓ Everything is up to date.[/green] {esc(sem)}{extra}")
        if self.auto:
            self.auto = False
            if todo:
                self.action_start()

    def action_page(self, step: int) -> None:
        if self.plan is None or self.running:
            return
        last = max(0, (getattr(self, "_matched_rows", 0) - 1) // self.page_size)
        self.page = max(0, min(last, self.page + step))
        used = self.used
        self.show_plan(self.plan)
        self.used = used

    def action_filter(self) -> None:
        field = self.query_one("#plan-filter", Input)
        field.display = True
        field.focus()

    @on(Input.Submitted, "#plan-filter")
    def filter_submitted(self) -> None:
        self.query_one(DataTable).focus()

    @on(Input.Changed, "#plan-filter")
    def filter_changed(self) -> None:
        self.page = 0
        if self.plan is not None and not self.running:
            used = self.used
            self.show_plan(self.plan)
            self.used = used

    def action_details(self) -> None:
        rows = getattr(self, "row_actions", [])
        index = self.query_one(DataTable).cursor_row
        if self.running or index >= len(rows):
            return
        action = rows[index]
        file = action.file
        body = f"Target: {action.dest or ''}\nReason: {action.reason or ''}"
        if file:
            body = (
                f"Course: {file.course_id}\nSource: {file.folder_path}/{file.name}\n"
                f"Size: {file.size} bytes\nMIME: {file.mime_type}\n"
                f"Changed: {file.chdate.isoformat()}\nDescription: {file.description}\n" + body
            )
        self.app.push_screen(DetailScreen(body))

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
                state.clear_failure(action.file.id)
                self.notify(f"{esc(action.file.name)} will never be downloaded.")
        self.action_refresh()

    def action_report(self) -> None:
        """Write a report about a wrongly sorted file, to check and send yourself."""
        table = self.query_one(DataTable)
        rows = getattr(self, "row_actions", [])
        if self.running or not rows or table.cursor_row >= len(rows):
            return
        action = rows[table.cursor_row]
        if action.file is None:
            return
        route = action.route
        if route and route.rule_id and not route.rule_id.startswith("auto:"):
            # A report helps the keyword lists; a rule of your own is yours to change.
            self.notify(
                f"Placed by your own rule {esc(route.rule_id)}: nothing to report. "
                "Change the rule in the config (r tests a file name).",
                severity="warning",
            )
            return
        title, body = report_text(action.file.folder_path, action.file.name, action.route)
        self.app.push_screen(ReportScreen(title, body))

    def action_add_rule(self) -> None:
        """A rule for files like the selected one: by its Stud.IP folder or its name."""
        table = self.query_one(DataTable)
        rows = getattr(self, "row_actions", [])
        if self.running or not rows or table.cursor_row >= len(rows):
            return
        action = rows[table.cursor_row]
        course = self.cfg.courses.get(action.course_id)
        if action.file is None or course is None:
            return
        route = action.route
        if (
            route
            and route.rule_id
            and not route.rule_id.startswith("auto:")
            and not route.by_default
        ):
            self.notify(
                f"Already placed by your rule {esc(route.rule_id)}; change it in the config.",
                severity="warning",
            )
            return
        file = action.file
        kinds: list[tuple[str, str]] = []
        if file.folder_path:
            kinds.append(
                ("folder", f"every file in the Stud.IP folder [b]{esc(file.folder_path)}[/b]")
            )
        if pattern := name_pattern(file.name):
            kinds.append(("name", f"every file named like [b]{esc(pattern)}[/b]"))
        if not kinds:
            self.notify(
                "This file has neither a Stud.IP folder nor a number in its name;"
                " Rules (r) can test a pattern by hand.",
                severity="warning",
            )
            return
        course_dir = self.cfg.course_dir(course)

        def write(by: str, target: str) -> None:
            rule = rule_from_file(
                tuple(r.id for r in course.rules), file.folder_path, file.name, by, target
            )
            _, problems = parse_rules([rule], "rule")
            if problems:
                self.notify(f"Not added: {esc(problems[0])}", severity="error")
                return
            try:
                append_blocks(
                    self.cfg.path,
                    [rule_block(course.id, rule, f"from the preview: {file.name}")],
                    "rule added in the preview",
                )
                self.app.reload_config()  # type: ignore[attr-defined]
            except Exception as exc:
                self.notify(f"Could not write: {esc(exc)}", severity="error")
                return
            self.notify(
                f"Rule [b]{esc(rule['id'])}[/b] added → {esc(target)}. Files synced before:"
                " Reorganize (o) moves them."
            )
            self.action_refresh()

        def typed(by: str, value: str | None) -> None:
            if not value:
                return
            value = value.strip().strip("/")
            if not value or ".." in Path(value).parts or Path(value).is_absolute():
                self.notify("Give a folder inside the course folder.", severity="error")
                return
            try:
                (course_dir / value).mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.notify(f"Folder not created: {esc(exc)}", severity="error")
                return
            write(by, value)

        def target_chosen(by: str, target: str | None) -> None:
            if target is None:
                return
            if target == NEW_FOLDER:
                self.app.push_screen(
                    TextScreen("New folder inside the course folder", "", esc(str(course_dir))),
                    lambda value: typed(by, value),
                )
                return
            write(by, target)

        def kind_chosen(by: str | None) -> None:
            if by is None:
                return
            folders = local_subdirs(course_dir)
            choices: list[tuple[str, str]] = [(f, esc(f)) for f in folders]
            choices.append((NEW_FOLDER, "[green]+ new folder…[/green]"))
            self.app.push_screen(
                ChoiceScreen(f"Into which folder of {esc(course.name or course.target)}?", choices),
                lambda target: target_chosen(by, target),
            )

        self.app.push_screen(
            ChoiceScreen(
                "A rule for which files?",
                kinds,
                f"like {esc(file.name)} - the rule is added to your config",
            ),
            kind_chosen,
        )

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
        if self.query_one("#plan-filter", Input).has_focus:
            self.query_one("#plan-filter", Input).display = False
            self.query_one(DataTable).focus()
            return
        if self.running:
            self.cancel = True
            self.status("[yellow]Stopping after the current file…[/yellow]")
        else:
            self.app.pop_screen()

    def action_start(self) -> None:
        if self.running or self.plan is None:
            return
        if self.used:  # never run a plan twice: its files exist now ("refusing to overwrite")
            self.status("Fetching course data from Stud.IP…")
            self.auto = True
            self.load_plan()
            return
        if not self._todo():
            return

        def confirmed(ok: bool | None) -> None:
            if ok:
                self.run_sync()

        self.app.push_screen(ConfirmScreen("Sync now?", plan_summary(self.plan)), confirmed)

    @work(thread=True, exclusive=True)
    def run_sync(self) -> None:
        assert self.plan is not None
        self.running = True
        self.used = True
        self.cancel = False
        self.app.call_from_thread(self._prepare_run, self._todo())

        def progress(action: Action) -> None:
            self.app.call_from_thread(self._log_action, action)
            if self.cancel:
                raise KeyboardInterrupt

        def failed(action: Action, message: str) -> None:
            self.app.call_from_thread(self._log_failed, action, message)
            if self.cancel:  # Esc works even when every file fails (network down)
                raise KeyboardInterrupt

        try:
            with self.client() as client, State(paths.state_db()) as state:
                result = execute_plan(
                    self.plan, self.cfg, state, client, on_progress=progress, on_failed=failed
                )
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

    def _log_failed(self, action: Action, message: str) -> None:
        self.query_one("#progress", ProgressBar).advance(1)
        name = action.dest.name if action.dest else ""
        self.query_one(RichLog).write(
            f"[red]✗[/] {esc(clip(name, 60))}  [dim]{esc(clip(self.short_paths(message), 80))}[/]"
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
            name = action.file.name if action.file else ""
            log.write(f"[red]✗ {esc(name)}: {esc(self.short_paths(msg))}[/red]")
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
