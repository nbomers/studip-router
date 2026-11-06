"""RulesView screens."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import (
    Footer,
    Input,
    Label,
    SelectionList,
    Static,
    TabbedContent,
    TabPane,
)

from ..api import ApiError, AuthFailed
from ..models import RemoteFile
from ..planner import build_router
from ..rename import RenameError
from ..report import esc
from ..rules import match_values
from ..suggest import (
    Suggestion,
    suggest_rules,
)
from ..tomlwrite import append_blocks, rule_block
from .common import View, clip, plural
from .widgets import (
    ConfirmScreen,
    course_color,
)


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
            with TabPane("Test a file name", id="tab-test"), VerticalScroll(classes="form"):
                yield Label("Course (part of the name or folder)")
                yield Input(id="test-course", placeholder="e.g. networks")
                yield Label("File name on Stud.IP")
                yield Input(id="test-name", placeholder="e.g. Blatt 3.pdf")
                yield Label("Stud.IP folder (optional)")
                yield Input(id="test-folder", placeholder="e.g. Übungsblätter")
                yield Static("Simulation: edit metadata below to match the real file.")
                yield Label("Size in MB")
                yield Input(value="0.095367431640625", id="test-size")
                yield Label("MIME type")
                yield Input(value="application/octet-stream", id="test-mime")
                yield Label("Changed (ISO date/time; no offset means local time)")
                yield Input(value=datetime.now().astimezone().isoformat(), id="test-date")
                yield Label("Description")
                yield Input(id="test-description")
                yield Label("Stud.IP folder type (optional)")
                yield Input(id="test-folder-type")
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
                    " · ".join(f"{key}={value}" for key, value in match_values(m).items())
                    or "everything"
                )
                target = "[dim]skip[/dim]" if r.skip else f"[b]{esc(r.target)}[/b]"
                rename = f"  [dim]rename {esc(r.rename)}[/dim]" if r.rename else ""
                lines.append(f"  {esc(i)}. [b]{esc(r.id)}[/b]: {esc(what)} → {target}{rename}")
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
                    for s in suggest_rules(
                        files,
                        self.cfg.course_dir(c),
                        {r.id for r in c.rules},
                        by_name=not self.cfg.auto_route,
                        own=self.cfg.keywords,
                    ):
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
        try:
            import math

            size_mb = float(self.query_one("#test-size", Input).value)
            if not math.isfinite(size_mb) or size_mb < 0:
                raise ValueError("Size must be finite and non-negative")
            changed = datetime.fromisoformat(self.query_one("#test-date", Input).value)
            if changed.tzinfo is None:
                changed = changed.astimezone()
        except ValueError as exc:
            result.update(f"[yellow]Invalid simulation metadata: {esc(exc)}[/yellow]")
            return
        file = RemoteFile(
            id="test",
            course_id=course.id,
            name=name,
            folder_path=folder,
            size=int(size_mb * 1024 * 1024),
            chdate=changed,
            mime_type=self.query_one("#test-mime", Input).value,
            description=self.query_one("#test-description", Input).value,
            folder_type=self.query_one("#test-folder-type", Input).value,
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
