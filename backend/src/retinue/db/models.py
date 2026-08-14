"""SQLAlchemy 2.0 models — the v0.1 slice of the locked schema (§17).

Conventions: UUIDv7 primary keys stored as 16-byte BLOBs (uuid on PG), integer
epoch-ms timestamps, JSON columns as TEXT (JSONB on PG). The agents tables ship
now (dormant until v0.2) because conversations FK into them.
"""

import uuid
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from retinue.core.ids import uuid7
from retinue.core.timeutil import now_ms
from retinue.db.types import JSONVal, UUIDBlob

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar = {dict[str, Any]: JSONVal, list[Any]: JSONVal}


# -- Identity -----------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(Text, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, default="member")  # owner|admin|member|viewer
    avatar_file_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
    session_version: Mapped[int] = mapped_column(Integer, default=1)  # bump = global logout
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class OidcIdentity(Base):
    __tablename__ = "oidc_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, index=True)
    token_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(BigInteger)
    rotated_to: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    revoked_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    scopes: Mapped[list[Any]] = mapped_column(JSONVal, default=list)
    last_used_at: Mapped[int | None] = mapped_column(BigInteger)
    expires_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


# -- Providers / models ---------------------------------------------------------


class Credential(Base):
    """§30.6 unified credentials table (v0.1 uses kind='llm' only).

    The secret payload (api_key & friends) is one AES-256-GCM blob.
    """

    __tablename__ = "credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDBlob, ForeignKey("users.id", ondelete="CASCADE")
    )  # NULL = org-global
    kind: Mapped[str] = mapped_column(Text, default="llm")  # llm|datasource|mcp|action|chatops
    provider: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    base_url: Mapped[str | None] = mapped_column(Text)
    data_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    data_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    last_verified_at: Mapped[int | None] = mapped_column(BigInteger)
    verify_status: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class ModelPolicy(Base):
    __tablename__ = "model_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    pattern: Mapped[str] = mapped_column(Text)  # fnmatch pattern over model id
    allow: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str | None] = mapped_column(Text)


# -- Agents (dormant until v0.2; conversations FK into these) -------------------


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, ForeignKey("users.id"))
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    avatar: Mapped[dict[str, Any] | None] = mapped_column(JSONVal)
    visibility: Mapped[str] = mapped_column(Text, default="private")  # private|org|public
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUIDBlob, ForeignKey("agents.id", ondelete="CASCADE")
    )
    version: Mapped[int] = mapped_column(Integer)
    system_prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
    tools: Mapped[list[Any]] = mapped_column(JSONVal, default=list)
    mcp_servers: Mapped[list[Any]] = mapped_column(JSONVal, default=list)
    collection_ids: Mapped[list[Any]] = mapped_column(JSONVal, default=list)
    starters: Mapped[list[Any]] = mapped_column(JSONVal, default=list)
    changelog: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUIDBlob)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


# -- Conversations & messages (parent_id → branching tree) ----------------------


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conv_user_recent", "user_id", "last_message_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str | None] = mapped_column(Text)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDBlob, ForeignKey("agents.id", ondelete="SET NULL")
    )
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDBlob, ForeignKey("agent_versions.id")
    )
    model_override: Mapped[str | None] = mapped_column(Text)
    params_override: Mapped[dict[str, Any] | None] = mapped_column(JSONVal)
    forked_from_message_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    folder: Mapped[str | None] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    is_incognito: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    last_message_at: Mapped[int | None] = mapped_column(BigInteger)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_msg_conv", "conversation_id", "created_at"),
        Index("ix_msg_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDBlob, ForeignKey("conversations.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDBlob, ForeignKey("messages.id")
    )  # branch tree; NULL = root
    role: Mapped[str] = mapped_column(Text)  # user|assistant|system|tool
    status: Mapped[str] = mapped_column(
        Text, default="complete"
    )  # streaming|complete|stopped|error
    model: Mapped[str | None] = mapped_column(Text)
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONVal)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class MessagePart(Base):
    __tablename__ = "message_parts"
    __table_args__ = (UniqueConstraint("message_id", "idx"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUIDBlob, ForeignKey("messages.id", ondelete="CASCADE")
    )
    idx: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(
        Text
    )  # text|thinking|tool_call|tool_result|image|file|citation
    content: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
    text_content: Mapped[str | None] = mapped_column(Text)  # feeds FTS (v0.5)


# -- Ops ------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_pick", "status", "run_at", "priority"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
    status: Mapped[str] = mapped_column(Text, default="queued")  # queued|running|done|failed|dead
    priority: Mapped[int] = mapped_column(Integer, default=5)
    run_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_at: Mapped[int | None] = mapped_column(BigInteger)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)


class JobSchedule(Base):
    __tablename__ = "job_schedules"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    cron: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
    last_run_at: Mapped[int | None] = mapped_column(BigInteger)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_user_time", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(UUIDBlob)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    ttft_ms: Mapped[int | None] = mapped_column(Integer)
    total_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUIDBlob, primary_key=True, default=uuid7)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUIDBlob)
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
    ip: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONVal, default=dict)
