"""Wire schemas. The TypeScript client is generated from the OpenAPI these
produce (D24) — changes must stay additive."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

# -- auth -----------------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    name: str | None = Field(None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None  # programmatic clients; browsers use the cookie


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    role: str
    settings: dict[str, Any]
    created_at: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserOut
    # Programmatic clients keep this; the browser SPA relies on the httpOnly
    # cookie instead and discards it.
    refresh_token: str | None = None


class UserPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    settings: dict[str, Any] | None = None


# -- conversations -----------------------------------------------------------


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    model_override: str | None
    params_override: dict[str, Any] | None
    folder: str | None
    pinned: bool
    is_archived: bool
    created_at: int
    updated_at: int
    last_message_at: int | None


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=300)
    model_override: str | None = Field(None, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    folder: str | None = Field(None, max_length=120)
    pinned: bool | None = None
    is_archived: bool | None = None
    model_override: str | None = Field(None, max_length=200)
    params_override: dict[str, Any] | None = None


class PartOut(BaseModel):
    idx: int
    type: str
    content: dict[str, Any]


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    parent_id: uuid.UUID | None
    role: str
    status: str
    model: str | None
    error: dict[str, Any] | None
    created_at: int
    parts: list[PartOut]


class MessageListOut(BaseModel):
    conversation_id: uuid.UUID
    messages: list[MessageOut]


# -- chat -------------------------------------------------------------------


class ChatParams(BaseModel):
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, gt=0, le=1)
    max_tokens: int | None = Field(None, ge=1, le=200_000)
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None

    def to_provider_params(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class ChatSendRequest(BaseModel):
    """message_id is the client-generated UUIDv7 of the user message and the
    idempotency key (§31.4a): retries attach to the existing stream."""

    message_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    text: str | None = Field(None, min_length=1, max_length=200_000)
    model: str | None = Field(None, max_length=200)
    params: ChatParams | None = None

    @model_validator(mode="after")
    def _text_required_for_new_messages(self) -> "ChatSendRequest":
        # text may be omitted only when re-sending an already-created message
        # (idempotent retry or edit-then-send); the server verifies existence.
        return self


class MessageEditRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    new_message_id: uuid.UUID | None = None  # client uuid7 for the sibling


class StopResponse(BaseModel):
    stopped: bool


# -- models / credentials ------------------------------------------------------


class ModelOut(BaseModel):
    id: str
    provider: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool
    supports_tools: bool
    input_cost_per_mtok: float | None
    output_cost_per_mtok: float | None


class CredentialCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1, max_length=4096)
    base_url: str | None = Field(None, max_length=1000)
    label: str | None = Field(None, max_length=200)
    org: bool = False  # org-global (admin only)


class CredentialOut(BaseModel):
    id: uuid.UUID
    provider: str
    label: str | None
    base_url: str | None
    org: bool
    key_hint: str
    created_at: int


# -- api keys ---------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: ["*"])
    expires_days: int | None = Field(None, ge=1, le=3650)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    scopes: list[str]
    last_used_at: int | None
    expires_at: int | None
    created_at: int


class ApiKeyCreated(ApiKeyOut):
    key: str  # shown exactly once


# -- usage ---------------------------------------------------------------------


class UsageTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    messages: int


class UsageByModel(UsageTotals):
    model: str


class UsageByDay(UsageTotals):
    day: str  # YYYY-MM-DD (UTC)


class UsageSummaryOut(BaseModel):
    days: int
    totals: UsageTotals
    by_model: list[UsageByModel]
    by_day: list[UsageByDay]


# -- system -----------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"]
    name: Literal["retinue"]
    version: str
