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
    agent_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    model_override: str | None
    params_override: dict[str, Any] | None
    forked_from_message_id: uuid.UUID | None = None
    folder: str | None
    pinned: bool
    is_archived: bool
    is_incognito: bool = False
    created_at: int
    updated_at: int
    last_message_at: int | None


class ConversationCreate(BaseModel):
    title: str | None = Field(None, max_length=300)
    model_override: str | None = Field(None, max_length=200)
    agent_id: uuid.UUID | None = None


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


class AttachmentOut(BaseModel):
    file_id: uuid.UUID
    kind: str
    name: str | None = None
    mime: str | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    parent_id: uuid.UUID | None
    role: str
    status: str
    model: str | None
    agent_version_id: uuid.UUID | None = None
    error: dict[str, Any] | None
    created_at: int
    parts: list[PartOut]
    attachments: list[AttachmentOut] = Field(default_factory=list)


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
    agent_id: uuid.UUID | None = None  # pin an agent when creating a conversation
    file_ids: list[uuid.UUID] = Field(default_factory=list, max_length=16)  # attachments

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


# -- agents (§9) --------------------------------------------------------------------


class AgentBehavior(BaseModel):
    """The immutable snapshot payload shared by create/new-version/import."""

    system_prompt: str = Field(max_length=200_000)
    model: str = Field(min_length=1, max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)
    tools: list[dict[str, Any]] = Field(default_factory=list, max_length=64)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    collection_ids: list[uuid.UUID] = Field(default_factory=list, max_length=32)
    starters: list[str] = Field(default_factory=list, max_length=8)
    changelog: str | None = Field(None, max_length=2000)


class AgentCreate(AgentBehavior):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    avatar: dict[str, Any] | None = None
    visibility: Literal["private", "org"] = "private"


class AgentPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    avatar: dict[str, Any] | None = None
    visibility: Literal["private", "org"] | None = None
    is_archived: bool | None = None


class AgentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    system_prompt: str
    model: str
    params: dict[str, Any]
    tools: list[dict[str, Any]]
    mcp_servers: list[dict[str, Any]]
    collection_ids: list[str]
    starters: list[str]
    changelog: str | None
    created_at: int


class AgentOut(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    avatar: dict[str, Any] | None
    visibility: str
    is_archived: bool
    owned: bool
    current_version: AgentVersionOut | None
    created_at: int
    updated_at: int


class AgentTestRequest(BaseModel):
    """Studio test bench (§6.6): ephemeral turn, nothing persisted."""

    messages: list[dict[str, Any]] = Field(min_length=1, max_length=64)
    behavior: AgentBehavior | None = None  # unsaved studio edits; None = current version


# -- files (§11) ---------------------------------------------------------------------


class FileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    mime: str
    size: int
    status: str
    meta: dict[str, Any]
    created_at: int


class UploadCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    size: int = Field(ge=1)
    mime: str | None = Field(None, max_length=200)
    blake3: str | None = Field(None, min_length=64, max_length=64)  # enables instant dedupe


class UploadCreateResponse(BaseModel):
    file_id: uuid.UUID
    upload_id: uuid.UUID | None  # None = deduped, file is already ready
    chunk_size: int
    expires_at: int
    already_exists: bool = False


class UploadCompleteRequest(BaseModel):
    blake3: str = Field(min_length=64, max_length=64)


# -- RAG collections (§10) --------------------------------------------------------------


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    embed_model: str | None = Field(None, max_length=200)  # None = server default


class CollectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    embed_model: str
    embed_dim: int
    visibility: str
    created_at: int


class CollectionFilesRequest(BaseModel):
    file_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class CollectionFileStatus(BaseModel):
    file_id: uuid.UUID
    name: str
    status: str
    chunks: int


class CollectionStatusOut(BaseModel):
    collection_id: uuid.UUID
    files: list[CollectionFileStatus]


class RagHit(BaseModel):
    chunk_id: uuid.UUID
    file_id: uuid.UUID
    file_name: str
    text: str
    score: float
    loc: dict[str, Any]


# -- memory (§14) -------------------------------------------------------------------


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content: str
    status: str
    source_conversation_id: uuid.UUID | None
    created_at: int
    updated_at: int


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class MemoryPatch(BaseModel):
    content: str | None = Field(None, min_length=1, max_length=4000)
    status: Literal["active", "disabled"] | None = None


# -- search (§13) --------------------------------------------------------------------


class SearchHit(BaseModel):
    kind: Literal["message", "conversation", "file", "agent"]
    id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    title: str | None = None
    snippet: str
    created_at: int | None = None


class SearchOut(BaseModel):
    query: str
    hits: list[SearchHit]


# -- shares (§18) -------------------------------------------------------------------


class ShareCreate(BaseModel):
    expires_days: int | None = Field(None, ge=1, le=365)


class ShareOut(BaseModel):
    id: uuid.UUID
    token: str
    url: str
    mode: str
    expires_at: int | None
    created_at: int


class SharedThreadOut(BaseModel):
    title: str | None
    created_at: int
    messages: list[MessageOut]


# -- MCP servers (§9.3) -----------------------------------------------------------


class McpServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    transport: Literal["stdio", "http"]
    command: str | None = Field(None, max_length=1000)  # stdio
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, str] = Field(default_factory=dict)  # encrypted at rest
    url: str | None = Field(None, max_length=1000)  # http
    headers: dict[str, str] = Field(default_factory=dict)  # encrypted at rest
    org: bool = False  # org-global (admin only)


class McpServerOut(BaseModel):
    id: uuid.UUID
    name: str
    transport: str
    spec: dict[str, Any]  # non-secret parts only
    has_secrets: bool
    enabled: bool
    org: bool
    last_status: dict[str, Any] | None
    created_at: int


class McpServerPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    enabled: bool | None = None


class McpToolOut(BaseModel):
    name: str
    description: str | None
    input_schema: dict[str, Any]


# -- OpenAPI actions (§9.4) ----------------------------------------------------------


class ActionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    spec: dict[str, Any]  # OpenAPI 3.x document
    auth: dict[str, Any] = Field(default_factory=dict)  # {type, ...secrets}
    host_allowlist: list[str] = Field(default_factory=list, max_length=32)


class ActionOut(BaseModel):
    id: uuid.UUID
    name: str
    operations: list[dict[str, Any]]  # [{name, method, path, summary}]
    host_allowlist: list[str]
    auth_type: str
    created_at: int


# -- data sources (§30) ---------------------------------------------------------------


class DataSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    engine: str = Field(min_length=1, max_length=50)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)  # encrypted at rest
    policy: dict[str, Any] = Field(default_factory=dict)


class DataSourcePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None
    policy: dict[str, Any] | None = None


class DataSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    engine: str
    engine_label: str
    config: dict[str, Any]  # non-secret only
    has_secrets: bool
    policy: dict[str, Any]
    status: str
    last_test: dict[str, Any] | None
    created_at: int


class DataSourceQueryRequest(BaseModel):
    statement: str = Field(min_length=1, max_length=20_000)
    limit: int | None = Field(None, ge=1, le=10_000)


class QueryResultOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: int
    note: str | None = None


# -- tool approvals (§9.2) -----------------------------------------------------------


class ApprovalRequest(BaseModel):
    call_id: str = Field(min_length=1, max_length=200)
    approve: bool


# -- admin (§18) ---------------------------------------------------------------------


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str | None
    role: str
    is_active: bool
    created_at: int


class AdminUserPatch(BaseModel):
    role: Literal["owner", "admin", "member", "viewer"] | None = None
    is_active: bool | None = None


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target: str | None
    meta: dict[str, Any]
    ip: str | None
    created_at: int


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    priority: int
    attempts: int
    last_error: str | None
    run_at: int
    created_at: int
    finished_at: int | None


class AdminSettingsOut(BaseModel):
    settings: dict[str, Any]


class AdminSettingsPatch(BaseModel):
    settings: dict[str, Any]


# -- system -----------------------------------------------------------------------


class HealthOut(BaseModel):
    status: Literal["ok"]
    name: Literal["retinue"]
    version: str
