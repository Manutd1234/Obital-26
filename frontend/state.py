from dataclasses import dataclass, field
import uuid


def _new_client_id() -> str:
    return f"obital-tui-{uuid.uuid4().hex[:12]}"


@dataclass
class AppState:
    account_id: str = "admin"
    lesson_id: str | None = None
    selected_doc_chunk_ids: list[str] = field(default_factory=list)
    chat_session_id: str | None = None
    reward_session_id: str | None = None
    unlock_token: str | None = None
    client_id: str = field(default_factory=_new_client_id)
