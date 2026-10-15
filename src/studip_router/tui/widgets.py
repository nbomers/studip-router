"""Dialogs shared by all TUI screens."""

from __future__ import annotations

from typing import Any, ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from ..config import Config


class ConfirmScreen(ModalScreen[bool]):
    """Yes/no question. y/Enter confirms, n/Esc cancels."""

    BINDINGS: ClassVar = [
        Binding("y,enter", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, question: str, detail: str = "") -> None:
        super().__init__()
        self.question = question
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.question, classes="question")
            if self.detail:
                yield Static(self.detail, classes="detail")
            with Horizontal(classes="buttons"):
                yield Button("Yes  y", variant="primary", id="yes")
                yield Button("No  n", id="no")

    def on_mount(self) -> None:
        self.query_one("#yes", Button).focus()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

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
            yield Label(f"Password for [b]{self.cfg.username}[/b]")
            yield Label(f"{self.cfg.base_url} · stored in your OS keychain", classes="dim")
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
  j / k  or  ↑ / ↓      move
  Enter                 open / confirm
  Esc  or  q            back
  ?                     this help

[b]Home[/b]
  s   Sync now (summary, then confirm)
  p   Preview: every file and where it goes
  w   What's new: recently synced files
  a   Announcements
  c   Courses: turn syncing on/off, add new courses
  r   Rules: suggestions, test a file name, your rules
  o   Reorganize already synced files (u: undo)
  m   Merge slide PDFs
  ,   Settings
  d   Doctor: check the setup

[b]Settings[/b]
  h / l  or  ← / →      change the value (saved immediately)
  e                     open the config file in $EDITOR

[b]Lists[/b]
  Space                 toggle (courses, rule suggestions)
  o / r / Space         open / reveal / Quick Look (What's new)
"""


class HelpScreen(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape,q,question_mark", "dismiss_help", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="dialog help"):
            yield Static(HELP)

    def action_dismiss_help(self) -> None:
        self.dismiss(None)
