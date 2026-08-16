from __future__ import annotations

from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, ListItem, ListView, Static


class DocChunkSelected(Message):
    def __init__(self, chunk_ids: list[str]) -> None:
        self.chunk_ids = chunk_ids
        super().__init__()


class DocPane(Vertical):
    def __init__(self) -> None:
        super().__init__(id="doc-pane")
        self.docs: list[dict] = []
        self.selected_doc_index = 0
        self.header = Label("Doc")
        self.list_view = ListView(id="doc-list")
        self.chunks = ListView(id="doc-chunks")
        self.detail = Static("No doc selected", id="doc-detail")

    def compose(self):
        yield self.header
        yield self.list_view
        yield self.chunks
        yield self.detail

    def set_docs(self, docs: list[dict]) -> None:
        self.docs = docs
        self._reset_list(self.list_view)
        for doc in docs:
            name = str(doc.get("name", "Untitled"))
            if len(name) > 42:
                name = name[:39].rstrip() + "..."
            self.list_view.append(ListItem(Label(name)))
        if docs:
            self.selected_doc_index = 0
            self._show_doc(0)
        else:
            self._reset_list(self.chunks)
            self.detail.update("No doc selected")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "doc-list":
            self._show_doc(event.list_view.index or 0)
        elif event.list_view.id == "doc-chunks":
            self._emit_selected_chunk(event.list_view.index or 0)

    def _show_doc(self, index: int) -> None:
        if not self.docs:
            return
        self.selected_doc_index = max(0, min(index, len(self.docs) - 1))
        doc = self.docs[self.selected_doc_index]
        chunks = doc.get("chunks", [])
        self._reset_list(self.chunks)
        for chunk in chunks:
            raw = str(chunk.get("text", ""))
            preview = " ".join(raw.split())
            if len(preview) > 90:
                preview = preview[:87].rstrip() + "..."
            self.chunks.append(ListItem(Label(preview)))
        self.detail.update(doc.get("name", ""))
        if chunks:
            self._show_chunk_detail(0)
            self.post_message(DocChunkSelected([chunks[0].get("chunk_id", "")]))

    def _emit_selected_chunk(self, idx: int) -> None:
        if not self.docs:
            return
        chunks = self.docs[self.selected_doc_index].get("chunks", [])
        if not chunks:
            return
        i = max(0, min(idx, len(chunks) - 1))
        self._show_chunk_detail(i)
        chunk_id = chunks[i].get("chunk_id", "")
        if chunk_id:
            self.post_message(DocChunkSelected([chunk_id]))

    def _show_chunk_detail(self, idx: int) -> None:
        chunks = self.docs[self.selected_doc_index].get("chunks", [])
        if not chunks:
            return
        i = max(0, min(idx, len(chunks) - 1))
        chunk = chunks[i]
        doc_name = str(self.docs[self.selected_doc_index].get("name", ""))
        chunk_id = str(chunk.get("chunk_id", ""))
        text = str(chunk.get("text", "")).strip()
        header = f"{doc_name}\nChunk: {chunk_id}" if chunk_id else doc_name
        body = text if text else "(empty chunk)"
        self.detail.update(f"{header}\n\n{body}")

    def select_doc_index(self, index: int) -> bool:
        if not self.docs:
            return False
        if index < 0 or index >= len(self.docs):
            return False
        self._show_doc(index)
        return True

    def select_chunk_index(self, index: int) -> bool:
        if not self.docs:
            return False
        chunks = self.docs[self.selected_doc_index].get("chunks", [])
        if index < 0 or index >= len(chunks):
            return False
        self._emit_selected_chunk(index)
        return True

    @staticmethod
    def _reset_list(list_view: ListView) -> None:
        try:
            list_view.clear()
        except Exception:
            list_view.remove_children("*")
