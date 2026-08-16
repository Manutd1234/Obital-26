from __future__ import annotations

from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Label, RichLog


class SendChatMessage(Message):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class RetrySessionRequested(Message):
    pass


class ChatPane(Vertical):
    def __init__(self) -> None:
        super().__init__(id="chat-pane")
        self.header = Label("Chat")
        self.transcript = RichLog(id="chat-transcript", markup=False, wrap=True, highlight=False)
        self.input = Input(placeholder="Type message (/retry)", id="chat-input")
        self._lines: list[str] = []
        self._streaming_line_index: int | None = None

    def compose(self):
        yield self.header
        yield self.transcript
        yield self.input

    def append_line(self, line: str) -> None:
        self._lines.append(line)
        self._redraw_transcript()

    def start_assistant_stream(self) -> None:
        self._streaming_line_index = len(self._lines)
        self._lines.append("Obital: ")
        self._redraw_transcript()

    def append_assistant_chunk(self, chunk: str) -> None:
        if self._streaming_line_index is None:
            self.start_assistant_stream()
        assert self._streaming_line_index is not None
        self._lines[self._streaming_line_index] += chunk
        self._redraw_transcript()

    def finish_assistant_stream(self) -> None:
        self._streaming_line_index = None

    def _redraw_transcript(self) -> None:
        self.transcript.clear()
        for line in self._lines:
            self.transcript.write(line)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "chat-input":
            return
        text = event.value.strip()
        if not text:
            return
        if text.lower() == "/retry":
            self.post_message(RetrySessionRequested())
            self.input.value = ""
            return
        self.post_message(SendChatMessage(text))
        self.input.value = ""
