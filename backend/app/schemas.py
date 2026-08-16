from datetime import datetime, UTC
from typing import Literal

from pydantic import BaseModel, Field


class LessonCheckpoint(BaseModel):
    title: str = ""
    difficulty: str = ""
    num_questions: int = 0
    pass_threshold_percent: float = 0.0
    question_mode: str = "mcq"
    markdown: str | None = None
    quiz: list[dict] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class LessonMaterial(BaseModel):
    material_id: str
    kind: Literal["chat", "quiz"]
    title: str
    created_at: str
    markdown: str | None = None
    quiz: dict | None = None


class Lesson(BaseModel):
    id: str
    title: str
    objective: str
    content_blocks: list[str]
    checkpoints: list[LessonCheckpoint] = Field(default_factory=list)
    materials: list[LessonMaterial] = Field(default_factory=list)
    status: str = "not_started"
    current_checkpoint: int = 0
    total_checkpoints: int = 0
    tags: list[str] = Field(default_factory=list)


class StartLessonRequest(BaseModel):
    topic: str
    title: str | None = None
    checkpoints: list[LessonCheckpoint] = Field(default_factory=list)


class StartLessonResponse(BaseModel):
    lesson_id: str
    title: str
    topic: str
    checkpoints: list[LessonCheckpoint]
    current_checkpoint_index: int = 0


class LessonAttemptRequest(BaseModel):
    checkpoint_index: int
    quiz_id: str
    score_percent: float
    passed: bool


class LessonAttemptResponse(BaseModel):
    lesson_id: str
    current_checkpoint_index: int
    completed: bool


class DocChunk(BaseModel):
    chunk_id: str
    text: str
    vector: list[float] | None = None
    tags: list[str] = Field(default_factory=list)


class DocEmbeddingMetadata(BaseModel):
    model: str | None = None
    dimension: int | None = None
    provider: str | None = None
    runtime: str | None = None
    chunk_count: int = 0


class Doc(BaseModel):
    id: str
    name: str
    uploaded_at: datetime
    chunks: list[DocChunk] = Field(default_factory=list)
    chunk_count: int = 0
    tags: list[str] = Field(default_factory=list)
    embedding: DocEmbeddingMetadata | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    text: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    lesson_id: str | None = None
    doc_chunk_ids: list[str] = Field(default_factory=list)


class ChatSession(BaseModel):
    id: str
    title: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    messages: list[ChatMessage] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    lesson_id: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str
    title: str = ""


class SendMessageRequest(BaseModel):
    message: str
    lesson_id: str | None = None
    doc_chunk_ids: list[str] = Field(default_factory=list)
    query_vector: list[float] | None = None
    embedding_model: str | None = None


class SendMessageResponse(BaseModel):
    session_id: str
    reply: str


class ConfirmActionRequest(BaseModel):
    action_id: str
    decision: Literal["approve", "cancel"]


class ConfirmActionResponse(BaseModel):
    session_id: str
    action_id: str
    status: str
    reply: str


class ChatSettings(BaseModel):
    system_prompt: str


class SettingsResponse(BaseModel):
    chat: ChatSettings


class ChatSettingsPatch(BaseModel):
    system_prompt: str | None = None


class SettingsPatchRequest(BaseModel):
    chat: ChatSettingsPatch


class RewardTrendPoint(BaseModel):
    x: int
    quiz_id: str
    submitted_at: str
    score_percent: float
    points_earned: int
    cumulative_points: int = 0
    topic_filter: str | None = None
    difficulty_filter: str | None = None


class RewardSummary(BaseModel):
    total_points: int
    total_quizzes: int
    total_correct: int
    total_answered: int
    accuracy_percent: float
    updated_at: str | None = None


class RewardSessionLogSummary(BaseModel):
    session_id: str
    started_at: str
    last_event_at: str
    points_delta: int
    quizzes_delta: int
    events: int


class RewardsDashboardResponse(BaseModel):
    username: str
    summary: RewardSummary
    trend: list[RewardTrendPoint] = Field(default_factory=list)
    session_logs: list[RewardSessionLogSummary] = Field(default_factory=list)


class StartRewardSessionResponse(BaseModel):
    session_id: str


class LeaderboardEntry(BaseModel):
    rank: int
    account_id: str
    display_name: str
    total_points: int
    total_quizzes: int
    accuracy_percent: float


class LeaderboardResponse(BaseModel):
    entries: list[LeaderboardEntry] = Field(default_factory=list)
    total_users: int = 0
    me: LeaderboardEntry | None = None


class WorkspaceTask(BaseModel):
    id: str
    text: str
    done: bool = False


class WorkspaceSession(BaseModel):
    id: str
    date: str  # local day key 'YYYY-MM-DD'
    duration_seconds: int = 0
    completed_at: str
    label: str | None = None


class WorkspaceCode(BaseModel):
    js: str = ""
    python: str = ""


class WorkspaceData(BaseModel):
    code: WorkspaceCode = Field(default_factory=WorkspaceCode)
    tasks: dict[str, list[WorkspaceTask]] = Field(default_factory=dict)
    sessions: list[WorkspaceSession] = Field(default_factory=list)


class PackTier(BaseModel):
    id: str
    label: str
    difficulty: str
    num_questions: int
    cost: int


class OwnedPack(BaseModel):
    id: str
    tier: str
    difficulty: str
    cost: int
    created_at: str
    opened: bool = False
    rewarded_at: str | None = None
    best_score_percent: float | None = None
    quiz: dict = Field(default_factory=dict)  # a QuizBuildResponse dump (the cards)


class ShopState(BaseModel):
    tiers: list[PackTier] = Field(default_factory=list)
    earned_xp: int = 0
    spent_points: int = 0
    available_xp: int = 0
    packs: list[OwnedPack] = Field(default_factory=list)


class PurchaseRequest(BaseModel):
    tier: str


class UnlockRequest(BaseModel):
    passphrase: str


class UnlockResponse(BaseModel):
    token: str

class AccountUsage(BaseModel):
    tokens_used: int = 0
    doc_count: int = 0
    lessons_count: int = 0
    storage_bytes: int = 0
    max_tokens: int = 500_000
    max_docs: int = 50
    max_lessons: int = 100
    max_storage_bytes: int = 104_857_600

class RecordTokensRequest(BaseModel):
    tokens: int = Field(ge=0, le=500_000)


class BinItem(BaseModel):
    id: str
    item_type: str
    item_id: str
    name: str
    deleted_at: datetime
    expires_at: datetime
    days_remaining: float


class FriendInfo(BaseModel):
    account_id: str
    display_name: str
    added_at: str


class FriendSearchResult(BaseModel):
    account_id: str
    display_name: str
    already_friend: bool


class FriendPresence(BaseModel):
    account_id: str
    display_name: str
    status: str = "offline"
    online: bool = False
    last_seen_at: str | None = None
