from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea


class SettingsScreen(ModalScreen[None]):
    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-modal {
        width: 80%;
        height: 80%;
        border: round #666666;
        padding: 1;
        background: $surface;
    }
    #system-prompt { height: 1fr; }
    #settings-actions { height: auto; }
    #settings-status { height: 3; }
    """

    class SaveRequested(Message):
        def __init__(self, screen: "SettingsScreen", system_prompt: str) -> None:
            self.screen = screen
            self.system_prompt = system_prompt
            super().__init__()

    class RefreshRequested(Message):
        def __init__(self, screen: "SettingsScreen") -> None:
            self.screen = screen
            super().__init__()

    def __init__(self, account_id: str) -> None:
        super().__init__()
        self.account_id = account_id
        self._prompt = TextArea("", id="system-prompt")
        self._status = Static(f"Account: {account_id}", id="settings-status")

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-modal"):
            yield Label(f"Settings ({self.account_id})")
            yield Label("System Prompt")
            yield self._prompt
            with Horizontal(id="settings-actions"):
                yield Button("Reload", id="settings-reload")
                yield Button("Save", id="settings-save", variant="primary")
                yield Button("Close", id="settings-close")
            yield self._status

    def set_prompt(self, value: str) -> None:
        self._prompt.text = value

    def set_status(self, value: str) -> None:
        self._status.update(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-close":
            self.dismiss(None)
            return
        if event.button.id == "settings-reload":
            self.post_message(self.RefreshRequested(self))
            return
        if event.button.id == "settings-save":
            self.post_message(self.SaveRequested(self, self._prompt.text))
