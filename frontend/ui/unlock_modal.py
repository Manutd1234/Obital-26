from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class UnlockModal(ModalScreen[str | None]):
    """Modal screen prompting for account passphrase to unlock."""

    CSS = """
    UnlockModal {
        align: center middle;
    }
    #unlock-modal {
        width: 50%;
        height: auto;
        border: round #666666;
        padding: 1 2;
        background: $surface;
    }
    #unlock-passphrase { width: 100%; }
    #unlock-actions { height: auto; margin-top: 1; }
    #unlock-status { height: 3; margin-top: 1; }
    """

    class Submitted(Message):
        def __init__(self, modal: "UnlockModal", passphrase: str) -> None:
            self.modal = modal
            self.passphrase = passphrase
            super().__init__()

    def __init__(self, account_id: str, purpose: str = "") -> None:
        super().__init__()
        self.account_id = account_id
        self.purpose = purpose
        self._input = Input("", id="unlock-passphrase", password=True, placeholder="Enter account passphrase")
        self._status = Static(f"Account: {account_id}", id="unlock-status")

    def compose(self) -> ComposeResult:
        with Vertical(id="unlock-modal"):
            yield Label(f"Account Passphrase ({self.account_id})")
            if self.purpose:
                yield Label(f"Purpose: {self.purpose}")
            yield self._input
            with Horizontal(id="unlock-actions"):
                yield Button("Unlock", id="unlock-submit", variant="primary")
                yield Button("Cancel", id="unlock-cancel")
            yield self._status

    def set_status(self, value: str) -> None:
        self._status.update(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "unlock-cancel":
            self.dismiss(None)
            return
        if event.button.id == "unlock-submit":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._submit()

    def _submit(self) -> None:
        passphrase = self._input.value.strip()
        if not passphrase:
            self.set_status("Passphrase cannot be empty.")
            return
        self.post_message(self.Submitted(self, passphrase))
