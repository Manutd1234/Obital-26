from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.services.db import Base

#this is just marking all the ids so they know what is what

class ChatSessionModel(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    messages: Mapped[list["ChatMessageModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageModel.seq_no",
    )

    __table_args__ = (
        Index("ix_chat_sessions_account_created_at", "account_id", "created_at"),
        Index("ix_chat_sessions_account_updated_at", "account_id", "updated_at"),
    )


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    seq_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    lesson_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    doc_chunk_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    session: Mapped["ChatSessionModel"] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("session_id", "seq_no", name="uq_chat_messages_session_seq"),
    )


class RewardSessionModel(Base):
    __tablename__ = "reward_sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    events: Mapped[list["RewardEventModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="RewardEventModel.id",
    )

    __table_args__ = (
        Index("ix_reward_sessions_account_created_at", "account_id", "created_at"),
    )


class RewardEventModel(Base):
    __tablename__ = "reward_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("reward_sessions.session_id", ondelete="CASCADE"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    total_points: Mapped[int] = mapped_column(Integer)
    total_quizzes: Mapped[int] = mapped_column(Integer)
    total_correct: Mapped[int] = mapped_column(Integer)
    total_answered: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
    session: Mapped["RewardSessionModel"] = relationship(back_populates="events")


class AccountKeyModel(Base):
    __tablename__ = "account_keys"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    salt_b64: Mapped[str] = mapped_column(String(256))
    argon2_params_json: Mapped[str] = mapped_column(Text)
    wrapped_dek_b64: Mapped[str] = mapped_column(Text)
    unlock_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_unlock_attempts: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_lockouts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failed_unlock_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class AccountUnlockSessionModel(Base):
    __tablename__ = "account_unlock_sessions"

    token_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    dek_ciphertext_b64: Mapped[str] = mapped_column(Text)
    unlock_version: Mapped[int] = mapped_column(Integer)
    key_material_fingerprint: Mapped[str] = mapped_column(String(128), default="")
    client_id: Mapped[str] = mapped_column(String(256), default="")
    user_agent: Mapped[str] = mapped_column(String(512), default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_account_unlock_sessions_account_expires_created", "account_id", "expires_at", "created_at"),
        Index("ix_account_unlock_sessions_account_created_at", "account_id", "created_at"),
    )


class AccountArtifactModel(Base):
    __tablename__ = "account_artifacts"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    relative_path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    hydration_status: Mapped[str] = mapped_column(String(24), default="full")

    __table_args__ = (
        Index("ix_account_artifacts_account_deleted", "account_id", "deleted_at"),
        Index("ix_account_artifacts_account_expires", "account_id", "expires_at"),
        Index("ix_account_artifacts_hydration_queue", "account_id", "hydration_status", "size_bytes", "updated_at"),
    )


class AccountRegistryRecordModel(Base):
    __tablename__ = "account_registry_records"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class RateLimitBucketModel(Base):
    __tablename__ = "rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(160), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_rate_limit_buckets_updated_at", "updated_at"),
    )


class BinItemModel(Base):
    __tablename__ = "bin_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64))
    item_type: Mapped[str] = mapped_column(String(32))
    item_id: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(512))
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        Index("ix_bin_items_account_id", "account_id"),
        Index("ix_bin_items_account_deleted_at", "account_id", "deleted_at"),
        Index("ix_bin_items_account_expires_at", "account_id", "expires_at"),
    )
