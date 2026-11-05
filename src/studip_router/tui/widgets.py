"""Dialogs shared by all TUI screens."""

from __future__ import annotations

import os
import time
from typing import Any, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .. import paths
from ..config import Config
from ..report import esc

# -- course colours -------------------------------------------------------------------
#
# Your own colours first: Stud.IP gives every course one of nine colour groups (the
# colours of "My courses" and the timetable). They are lightened here so they stay
# readable on a dark terminal. Courses without a group get distinct colours in the
# order of the config, so no two of your courses share one.

STUDIP_GROUP_COLORS = (
    "#9d77b5",  # 0 purple   (Stud.IP #682c8b)
    "#cc77aa",  # 1 pink     (#b02e7c)
    "#65beb9",  # 2 teal     (#129c94)
    "#f7a159",  # 3 orange   (#f26e00)
    "#59ad65",  # 4 green    (#008512)
    "#c69687",  # 5 brown    (#a85d45)
    "#a1c964",  # 6 lime     (#6ead10)
    "#e45959",  # 7 red      (#d60000)
    "#ffd47b",  # 8 yellow   (#ffbd33)
)
FALLBACK_COLORS = (
    "#61afef",
    "#c678dd",
    "#56b6c2",
    "#d19a66",
    "#e88fc9",
    "#7aa2f7",
    "#f5a97f",
    "#b4befe",
    "#94e2d5",
    "#f2cdcd",
)
# For light themes: the original Stud.IP colours and darker fallbacks, each darkened
# to a contrast of at least 4.6:1 on light grey (#efefef; WCAG AA for text is 4.5:1).
STUDIP_GROUP_COLORS_LIGHT = (
    "#682c8b",
    "#b02e7c",
    "#0e7671",
    "#b04f00",
    "#007010",
    "#8f4e3a",
    "#487209",
    "#c00000",
    "#8a5f00",
)
FALLBACK_COLORS_LIGHT = (
    "#1d69b5",
    "#8e44ad",
    "#13727e",
    "#985c27",
    "#b03a86",
    "#3557c4",
    "#a35126",
    "#5b5fc7",
    "#167767",
    "#a14d4d",
)
_groups: dict[str, int] = {}
_order: dict[str, int] = {}
_light = False


def use_light_colors(light: bool) -> None:
    """Called when the theme changes: course colours for a light or dark background."""
    global _light
    _light = light


def _colors_file():
    from .. import paths

    # One file per profile, like the state database: two universities, two sets.
    name = f"course-colors-{paths.profile()}.json" if paths.profile() else "course-colors.json"
    return paths.state_dir() / name


def set_course_order(course_ids: list[str]) -> None:
    """Courses in config order: each gets its own fallback colour."""
    _order.clear()
    _order.update({cid: i for i, cid in enumerate(course_ids)})


def load_course_groups() -> None:
    import contextlib
    import json

    with contextlib.suppress(OSError, ValueError):
        data = json.loads(_colors_file().read_text())
        if isinstance(data, dict):
            _groups.clear()
            _groups.update(
                {
                    k: v
                    for k, v in data.items()
                    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 8
                }
            )


def save_course_groups(groups: dict[str, int]) -> bool:
    """Remember Stud.IP's colour groups; True if anything changed."""
    import contextlib
    import json

    clean = {k: v for k, v in groups.items() if 0 <= v <= 8}
    if not clean or clean == _groups:
        return False
    # No clear() first: a screen drawn meanwhile must not fall back to other colours.
    for gone in set(_groups) - set(clean):
        _groups.pop(gone, None)
    _groups.update(clean)
    with contextlib.suppress(OSError):
        path = _colors_file()
        paths.private_dir(path.parent)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(clean))
        os.replace(tmp, path)  # never a half-written file
    return True


def course_color(course_id: str) -> str:
    """The colour of a course on every screen."""
    groups = STUDIP_GROUP_COLORS_LIGHT if _light else STUDIP_GROUP_COLORS
    fallback = FALLBACK_COLORS_LIGHT if _light else FALLBACK_COLORS
    if course_id in _groups:
        return groups[_groups[course_id]]
    if course_id in _order:
        return fallback[_order[course_id] % len(fallback)]
    import hashlib

    digest = hashlib.sha1(course_id.encode(), usedforsecurity=False).digest()
    return fallback[digest[0] % len(fallback)]


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no question. y/Enter confirms, n/Esc cancels.

    Enter right after the dialog opened is ignored: a double Enter (one to start,
    one by accident) must not confirm before the summary could be read.
    """

    BINDINGS: ClassVar = [
        Binding("y", "answer(True)", "Yes"),
        Binding("enter", "enter", "Yes", show=False),
        Binding("n,escape", "answer(False)", "No"),
    ]
    ENTER_DELAY = 0.4  # seconds

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail
        self._opened = time.monotonic()

    def _too_early(self) -> bool:
        return time.monotonic() - self._opened < self.ENTER_DELAY

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question, classes="question")
            if self.detail:
                yield Static(self.detail, classes="detail")
            with Horizontal(classes="buttons"):
                yield Button("Yes  y", variant="primary", id="yes")
                yield Button("No  n", id="no")

    def on_mount(self) -> None:
        self._opened = time.monotonic()
        self.query_one("#yes", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "yes" and self._too_early():
            return
        self.dismiss(event.button.id == "yes")

    def action_enter(self) -> None:
        if not self._too_early():
            self.dismiss(True)

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class PasswordScreen(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, cfg: Config, error: str = "") -> None:
        super().__init__()
        self.cfg = cfg
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"Password for [b]{esc(self.cfg.username)}[/b]")
            yield Label(f"{esc(self.cfg.base_url)} · stored in your OS keychain", classes="dim")
            if self.error:
                yield Label(self.error, classes="error")
            yield Input(password=True, placeholder="password", id="password")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextScreen(ModalScreen[str | None]):
    """Edit one line of text; Enter saves, Esc cancels."""

    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str, detail: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.value = value
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(f"[b]{self.title_text}[/b]")
            if self.detail:
                yield Label(self.detail, classes="dim")
            yield Input(self.value, id="text")

    @on(Input.Submitted)
    def submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceScreen(ModalScreen[Any]):
    """Pick one of several values; j/k or arrows, Enter selects, Esc cancels."""

    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "down", show=False),
        Binding("k", "up", show=False),
    ]

    def __init__(self, title: str, choices: list[tuple[Any, str]], detail: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="question")
            if self.detail:
                yield Static(self.detail, classes="detail")
            yield OptionList(*[Option(label) for _, label in self.choices], id="choices")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    @on(OptionList.OptionSelected)
    def selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.choices[event.option_index][0])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_down(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_up(self) -> None:
        self.query_one(OptionList).action_cursor_up()


HELP = """\
[b]Everywhere[/b]
  ↑ ↓  or  j k     move
  Enter            open / confirm
  Esc              back
  ,                settings
  ?                this help

[b]Home[/b]
  Enter  do the suggested step
  s  sync          p  preview
  w  what's new    r  rules
  a  announcements o reorganize (u undo)
  t  dates         m  merge slides
  c  courses       d  doctor
  +  more / fewer

[b]Lists[/b]
  Space      tick / untick
  x          announcements: mark all read
  ← →        change a setting (saved at once)
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape,q,question_mark", "dismiss_help", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help"):
            yield Static(HELP)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
