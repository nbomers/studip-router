"""Settings screen: one row per setting, options side by side (Monkeytype style).

h/l or ←/→ change the focused row, j/k move between rows. Every change is
validated and written to the config file immediately, keeping its comments.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Footer, Static

from .. import auth, paths, schedule
from ..config import Config, ConfigError
from ..config_edit import set_value
from .widgets import ConfirmScreen, TextScreen

THEMES = [
    "tokyo-night",
    "catppuccin-mocha",
    "nord",
    "gruvbox",
    "dracula",
    "rose-pine",
    "textual-dark",
    "textual-light",
]
AUTO_SYNC = [
    (0, "off"),
    (30, "30 min"),
    (60, "1 h"),
    (120, "2 h"),
    (360, "6 h"),
    (720, "12 h"),
    (1440, "daily"),
]


@dataclass
class Setting:
    label: str
    help: str
    options: list[tuple[Any, str]]
    current: Callable[[Config], Any]
    keys: tuple[str, ...] = ()
    to_config: Callable[[Config, Any], Any] = field(default=lambda _cfg, v: v)
    special: str = ""  # handled by the screen instead of a plain config write
    parse: Callable[[str], Any] | None = None  # free text (Enter edits) instead of options


def _home(path: Any) -> str:
    text = str(path)
    home = os.path.expanduser("~")
    return "~" + text[len(home) :] if text.startswith(home) else text


def _list(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


ON_OFF = [(True, "on"), (False, "off")]


def _videos(cfg: Config, on: bool) -> list[str]:
    others = [m for m in cfg.exclude.mime if m != "video/*"]
    return ([*others, "video/*"]) if on else others


SECTIONS: list[tuple[str, list[Setting]]] = [
    (
        "folders",
        [
            Setting(
                "course folders in",
                "the folder that holds one folder per course · Enter to change",
                [],
                lambda c: _home(c.root),
                ("sync", "root"),
                parse=str,
            ),
            Setting(
                "new course gets",
                "subfolders of a new course folder (c → + new folder) · Enter to change",
                [],
                lambda c: ", ".join(c.course_folders),
                ("sync", "course_folders"),
                parse=_list,
            ),
            Setting(
                "default folder",
                "for files that no rule and no folder name places · Enter to change",
                [],
                lambda c: c.default_target,
                ("sync", "default_target"),
                parse=str,
            ),
        ],
    ),
    (
        "sync",
        [
            Setting(
                "semester",
                "which courses sync looks at",
                [("current", "current"), ("all", "all")],
                lambda c: c.semester,
                ("sync", "semester"),
            ),
            Setting(
                "changed files",
                "a file was updated on Stud.IP",
                [("version", "keep both (_v2)"), ("overwrite", "replace"), ("skip", "skip")],
                lambda c: c.on_update,
                ("sync", "on_update"),
            ),
            Setting(
                "name conflicts",
                "two files want the same name",
                [("suffix", "add _2"), ("skip", "skip"), ("error", "report")],
                lambda c: c.on_conflict,
                ("sync", "on_conflict"),
            ),
            Setting(
                "follow renames",
                "rename the local copy when a file is renamed on Stud.IP",
                ON_OFF,
                lambda c: c.follow_renames,
                ("sync", "follow_renames"),
            ),
            Setting(
                "auto-route",
                "no rule matches: use the Stud.IP folder name (Übung, Folien, ...)",
                ON_OFF,
                lambda c: c.auto_route,
                ("sync", "auto_route"),
            ),
            Setting(
                "watch dates",
                "report cancelled, moved and re-roomed course dates after a sync",
                ON_OFF,
                lambda c: c.watch_dates,
                ("sync", "watch_dates"),
            ),
            Setting(
                "create folders",
                "create missing target folders of rules",
                ON_OFF,
                lambda c: c.create_missing_dirs,
                ("sync", "create_missing_dirs"),
            ),
            Setting(
                "auto-sync",
                "background sync with notifications (launchd/systemd)",
                AUTO_SYNC,
                lambda c: _auto_sync_minutes(),
                special="schedule",
            ),
            Setting(
                "notifications",
                "desktop notification after background syncs",
                ON_OFF,
                lambda c: c.notify,
                ("sync", "notify"),
            ),
        ],
    ),
    (
        "files",
        [
            Setting(
                "skip videos",
                "never download video files",
                ON_OFF,
                lambda c: "video/*" in c.exclude.mime,
                ("sync", "exclude", "mime"),
                to_config=_videos,
            ),
            Setting(
                "size limit",
                "skip larger files (sync --include-large overrides)",
                [
                    (0, "none"),
                    (50, "50 MB"),
                    (100, "100 MB"),
                    (200, "200 MB"),
                    (500, "500 MB"),
                    (1000, "1 GB"),
                ],
                lambda c: int(c.exclude.max_size_mb or 0),
                ("sync", "exclude", "max_size_mb"),
            ),
            Setting(
                "courseware",
                "also sync files from Courseware pages (needs more requests)",
                ON_OFF,
                lambda c: c.courseware,
                ("sync", "courseware"),
            ),
            Setting(
                "unzip archives",
                "extract .zip files next to the archive",
                ON_OFF,
                lambda c: c.unzip,
                ("sync", "unzip"),
            ),
        ],
    ),
    (
        "names",
        [
            Setting(
                "number padding",
                "lecture-1.pdf → lecture-01.pdf",
                [(0, "off"), (2, "01"), (3, "001")],
                lambda c: c.rename.pad_numbers,
                ("rename", "pad_numbers"),
            ),
            Setting(
                "spaces",
                "how spaces in file names are written",
                [("keep", "keep"), ("_", "under_score"), ("-", "dash-ed")],
                lambda c: c.rename.spaces,
                ("rename", "spaces"),
            ),
            Setting(
                "umlauts",
                "ä ö ü ß",
                [("keep", "keep"), ("transliterate", "ae oe ue ss")],
                lambda c: c.rename.umlauts,
                ("rename", "umlauts"),
            ),
            Setting(
                "lower case",
                "write all names in lower case",
                ON_OFF,
                lambda c: c.rename.lowercase,
                ("rename", "lowercase"),
            ),
            Setting(
                "portable names",
                'replace : * ? " < > | (Windows, OneDrive, iCloud)',
                ON_OFF,
                lambda c: c.rename.portable,
                ("rename", "portable"),
            ),
        ],
    ),
    (
        "app",
        [
            Setting(
                "theme",
                "colours of this interface",
                [(t, t) for t in THEMES],
                lambda c: c.ui_theme,
                ("ui", "theme"),
                special="theme",
            ),
        ],
    ),
]


def _auto_sync_minutes() -> int:
    try:
        st = schedule.status(paths.profile())
    except OSError:
        return 0
    return (st.interval_minutes or 120) if st.installed else 0


class SettingRow(Widget, can_focus=True):
    """label · help on the left, options side by side on the right."""

    DEFAULT_CSS = """
    SettingRow { height: 2; padding: 0 2; }
    SettingRow:focus { background: $boost; }
    """
    BINDINGS: ClassVar = [
        Binding("left,h", "move(-1)", "←", show=False),
        Binding("right,l", "move(1)", "→", show=False),
        Binding("enter", "move(1)", "Change", show=False),
    ]

    class Changed(Message):
        def __init__(self, row: SettingRow, value: Any) -> None:
            super().__init__()
            self.row = row
            self.value = value

    def __init__(self, setting: Setting, value: Any) -> None:
        super().__init__()
        self.setting = setting
        self.value = value
        self._spans: list[tuple[int, int, Any]] = []

    def set_value(self, value: Any) -> None:
        self.value = value
        self.refresh()

    def _index(self) -> int:
        values = [v for v, _ in self.setting.options]
        return values.index(self.value) if self.value in values else -1

    def render(self) -> Text:
        focused = self.has_focus
        accent = getattr(self.app.current_theme, "accent", None) or "magenta"
        text = Text()
        text.append("▸ " if focused else "  ", style=f"bold {accent}" if focused else "")
        text.append(f"{self.setting.label:<18}", style="bold" if focused else "")
        self._spans = []
        if self.setting.parse is not None:  # free text
            text.append(f" {self.value} ", style=f"bold black on {accent}" if focused else "bold")
            self._spans = [(len(text) - len(str(self.value)) - 2, len(text), "<edit>")]
            text.append(f"\n    {self.setting.help}", style="dim italic")
            return text
        full = sum(len(label) + 3 for _, label in self.setting.options)
        if full > max(self.size.width - 24, 20):  # too wide: show "◂ value ▸" instead
            index = self._index()
            label = self.setting.options[index][1] if index >= 0 else str(self.value)
            prev_start = len(text)
            text.append(" ◂ ", style="bold" if focused else "dim")
            text.append(f" {label} ", style=f"bold black on {accent}")
            next_start = len(text)
            text.append(" ▸ ", style="bold" if focused else "dim")
            text.append(f" {index + 1}/{len(self.setting.options)}", style="dim")
            self._spans = [
                (prev_start, prev_start + 3, "<prev>"),
                (next_start, next_start + 3, "<next>"),
            ]
            text.append(f"\n    {self.setting.help}", style="dim italic")
            return text
        for value, label in self.setting.options:
            start = len(text)
            selected = value == self.value
            if selected:
                text.append(f" {label} ", style=f"bold black on {accent}")
            else:
                text.append(f" {label} ", style="dim" if not focused else "")
            self._spans.append((start, len(text), value))
            text.append(" ")
        if self._index() == -1:
            text.append(f" (custom: {self.value})", style="italic dim")
        text.append(f"\n    {self.setting.help}", style="dim italic")
        return text

    def action_move(self, step: int) -> None:
        if self.setting.parse is not None:
            self.post_message(self.Changed(self, "<edit>"))
            return
        options = self.setting.options
        index = self._index()
        new = options[(index + step) % len(options)][0] if index >= 0 else options[0][0]
        self.post_message(self.Changed(self, new))

    def on_click(self, event: events.Click) -> None:
        if event.y != 0:
            return
        for start, end, value in self._spans:
            if not start <= event.x < end:
                continue
            if value == "<edit>":
                self.action_move(1)
            elif value == "<prev>":
                self.action_move(-1)
            elif value == "<next>":
                self.action_move(1)
            elif value != self.value:
                self.post_message(self.Changed(self, value))

    def on_focus(self) -> None:
        self.scroll_visible(animate=False)
        self.refresh()

    def on_blur(self) -> None:
        self.refresh()


class _SettingsList(VerticalScroll, can_focus=False):
    """Scrolls, but j/k focus moves between the rows only."""


class SettingsScreen(Screen):
    TITLE = "Settings"
    BINDINGS: ClassVar = [
        Binding("escape,q", "app.pop_screen", "Back"),
        Binding("j,down", "app.focus_next", "Down", show=False),
        Binding("k,up", "app.focus_previous", "Up", show=False),
        Binding("h,left", "change(-1)", "← change"),
        Binding("l,right", "change(1)", "→"),
        Binding("e", "edit", "Edit config file"),
        Binding("L", "login", "Log in"),
        Binding("O", "logout", "Log out"),
    ]

    def compose(self) -> ComposeResult:
        cfg: Config = self.app.cfg  # type: ignore[attr-defined]
        yield Static(
            " [b]Settings[/b]  [dim]h/l change · j/k move · saved immediately[/dim]",
            classes="view-title",
        )
        with _SettingsList(id="settings"):
            yield Static(id="account", classes="section")
            for section, settings in SECTIONS:
                yield Static(f"  {section}", classes="section-title")
                for setting in settings:
                    yield SettingRow(setting, setting.current(cfg))
            yield Static(f"\n  [dim]config: {cfg.path}[/dim]", id="config-path")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_account()
        self.query(SettingRow).first().focus(scroll_visible=False)
        self.query_one("#settings").scroll_home(animate=False)

    def refresh_account(self) -> None:
        cfg: Config = self.app.cfg  # type: ignore[attr-defined]
        stored = auth.has_password(cfg.base_url, cfg.username)
        state = "[green]● password stored[/green]" if stored else "[yellow]○ not logged in[/yellow]"
        self.query_one("#account", Static).update(
            f"  [b]account[/b]   {cfg.username} @ {cfg.base_url.split('//')[-1]}   {state}"
            "   [dim]L log in · O log out[/dim]"
        )

    def action_change(self, step: int) -> None:
        focused = self.focused
        if isinstance(focused, SettingRow):
            focused.action_move(step)

    def on_setting_row_changed(self, message: SettingRow.Changed) -> None:
        row, value = message.row, message.value
        setting = row.setting
        if setting.special == "schedule":
            self._change_schedule(row, value)
            return
        if setting.parse is not None:
            self._edit_text(row)
            return
        cfg: Config = self.app.cfg  # type: ignore[attr-defined]
        try:
            new_cfg = set_value(cfg.path, setting.keys, setting.to_config(cfg, value))
        except (ConfigError, OSError) as exc:
            self.notify(f"Not saved: {exc}", severity="error")
            return
        self.app.cfg = new_cfg  # type: ignore[attr-defined]
        row.set_value(value)
        if setting.special == "theme":
            self.app.theme = value

    def _edit_text(self, row: SettingRow) -> None:
        setting = row.setting
        cfg: Config = self.app.cfg  # type: ignore[attr-defined]

        def save(text: str | None) -> None:
            if text is None or setting.parse is None:
                return
            try:
                new_cfg = set_value(cfg.path, setting.keys, setting.parse(text))
            except (ConfigError, OSError) as exc:
                self.notify(f"Not saved: {exc}", severity="error")
                return
            self.app.cfg = new_cfg  # type: ignore[attr-defined]
            row.set_value(setting.current(new_cfg))

        self.app.push_screen(TextScreen(setting.label, str(row.value), setting.help), save)

    def _change_schedule(self, row: SettingRow, minutes: int) -> None:
        def apply(ok: bool | None) -> None:
            if not ok:
                return
            try:
                if minutes:
                    schedule.install(minutes, paths.profile())
                else:
                    schedule.uninstall(paths.profile())
            except (OSError, subprocess.CalledProcessError) as exc:
                self.notify(f"Auto-sync: {exc}", severity="error")
                return
            row.set_value(minutes)
            self.notify("Auto-sync " + (f"every {minutes} min" if minutes else "turned off"))

        question = (
            f"Run a background sync every {minutes} minutes?"
            if minutes
            else "Turn off the background sync?"
        )
        detail = schedule.executable_warning(schedule.executable()) or ""
        self.app.push_screen(ConfirmScreen(question, detail), apply)

    def action_edit(self) -> None:
        cfg: Config = self.app.cfg  # type: ignore[attr-defined]
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        with self.app.suspend():
            subprocess.run([*editor.split(), str(cfg.path)], check=False)
        self.app.reload_config()  # type: ignore[attr-defined]
        if self.app.cfg is None:  # type: ignore[attr-defined]
            self.notify(
                f"Config now invalid: {self.app.config_error}",  # type: ignore[attr-defined]
                severity="error",
                timeout=10,
            )
            return
        for row in self.query(SettingRow):
            row.set_value(row.setting.current(self.app.cfg))  # type: ignore[attr-defined]

    def action_login(self) -> None:
        self.app.ask_password(self.refresh_account)  # type: ignore[attr-defined]

    def action_logout(self) -> None:
        cfg: Config = self.app.cfg  # type: ignore[attr-defined]

        def logout(ok: bool | None) -> None:
            if ok:
                try:
                    auth.delete_password(cfg.base_url, cfg.username)
                except auth.AuthError as exc:
                    self.notify(str(exc), severity="error")
                self.refresh_account()

        self.app.push_screen(ConfirmScreen("Remove the stored password?"), logout)
