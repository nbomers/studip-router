"""Shared screen base, display helpers and navigation constants."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.cells import cell_len, set_cell_size
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import (
    ProgressBar,
    Static,
)

from .. import paths
from ..api import ApiError, AuthFailed, StudipClient
from ..config import Config
from ..planner import Action, Kind
from ..report import esc, summarize
from ..sync import (
    SyncPlan,
)


def clip(text: str, width: int) -> str:
    """Cut long names for display: "Introduction to Quantum Coo…"."""
    if cell_len(text) <= width:
        return text
    if width < 3:
        return set_cell_size(text, max(width, 0))
    left = (width - 1) // 2
    tail = text
    while cell_len(tail) > width - left - 1:
        tail = tail[1:]
    return set_cell_size(text, left).rstrip() + "…" + tail


def plural(n: int, word: str, many: str = "") -> str:
    """ "[b]3[/b] files" - the number bold, the noun pluralised."""
    return f"[b]{n}[/b] {word if n == 1 else (many or word + 's')}"


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


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

DOCUMENTS = frozenset(
    {
        ".pdf",
        ".txt",
        ".md",
        ".csv",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".odt",
        ".ods",
        ".odp",
    }
)


class View(Screen):
    """Full-screen view: title bar, Esc/q back, j/k move."""

    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("comma", "app.open_settings", "Settings"),
        Binding("question_mark", "app.show_help", "Help", show=False),  # "? everywhere"
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
        try:
            for index, item in enumerate(items):
                self.busy(
                    index,
                    len(items),
                    f"Reading [b]{index + 1}/{len(items)}[/b]: {esc(clip(label(item), 50))}…",
                )
                yield item
        finally:  # also when the caller stops early or fails: never a stuck bar
            self.idle()

    def short_paths(self, text: str) -> str:
        """Error texts name absolute paths; inside the sync folder the rest is enough."""
        root = str(self.cfg.root)
        return text.replace(root + "/", "").replace(str(Path.home()), "~")

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
    other = sum(counts.get(k, 0) for k in ("adopt", "refresh", "rename", "mkdir", "unzip"))
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


EXCLUDE = "\0exclude"  # cannot be a folder name

NEW_FOLDER = "\0new"

NO_MERGE = "\0none"


def short_path(text: str) -> str:
    """Long iCloud and home paths in a text, readable: "iCloud Drive/Uni/…", "~/…"."""
    home = str(Path.home())
    icloud = f"{home}/Library/Mobile Documents/com~apple~CloudDocs"
    return text.replace(icloud, "iCloud Drive").replace(home, "~")
