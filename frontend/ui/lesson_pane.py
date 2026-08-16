from __future__ import annotations

from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static


class LessonSelected(Message):
    def __init__(self, lesson: dict) -> None:
        self.lesson = lesson
        super().__init__()


class LessonPane(Vertical):
    def __init__(self) -> None:
        super().__init__(id="lesson-pane")
        self.lessons: list[dict] = []
        self.selected_index = 0
        self.header = Label("Lesson")
        self.list_view = ListView(id="lesson-list")
        self.detail = Static("No lesson loaded", id="lesson-detail")

    def compose(self):
        yield self.header
        yield self.list_view
        yield self.detail

    def set_lessons(self, lessons: list[dict]) -> None:
        self.lessons = lessons
        self._reset_list()
        for lesson in lessons:
            self.list_view.append(ListItem(Label(lesson.get("title", "Untitled"))))
        if lessons:
            self.selected_index = 0
            self._show_selected(0)
        else:
            self.detail.update("No lesson loaded")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self._show_selected(event.list_view.index or 0)

    def _show_selected(self, index: int) -> None:
        if not self.lessons:
            return
        self.selected_index = max(0, min(index, len(self.lessons) - 1))
        lesson = self.lessons[self.selected_index]
        blocks = lesson.get("content_blocks", [])
        text = f"{lesson.get('title', '')}\n\nObjective: {lesson.get('objective', '')}\n\n" + "\n".join(
            f"- {b}" for b in blocks
        )
        self.detail.update(text)
        self.post_message(LessonSelected(lesson))

    def select_index(self, index: int) -> bool:
        if not self.lessons:
            return False
        if index < 0 or index >= len(self.lessons):
            return False
        self._show_selected(index)
        return True

    def _reset_list(self) -> None:
        try:
            self.list_view.clear()
        except Exception:
            self.list_view.remove_children("*")
