from __future__ import annotations

from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Label, Static, TextArea


class SaveSettingsRequested(Message):
    def __init__(self, system_prompt: str) -> None:
        self.system_prompt = system_prompt
        super().__init__()


class RefreshSettingsRequested(Message):
    pass


class SettingsPane(Vertical):
    def __init__(self) -> None:
        super().__init__(id="settings-pane")
        self.header = Label("Settings")
        self.system_prompt = TextArea("", id="system-prompt")
        self.status = Static("Prompt loaded from account settings.", id="settings-status")

    def compose(self):
        yield self.header
        yield Label("System Prompt")
        yield self.system_prompt
        with Horizontal(id="settings-actions"):
            yield Button("Reload", id="settings-reload")
            yield Button("Save", id="settings-save", variant="primary")
        yield self.status

    def set_prompt(self, value: str) -> None:
        self.system_prompt.text = value
        self.status.update("Prompt loaded.")

    def set_status(self, value: str) -> None:
        self.status.update(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-reload":
            self.post_message(RefreshSettingsRequested())
            return
        if event.button.id == "settings-save":
            self.post_message(SaveSettingsRequested(self.system_prompt.text))
