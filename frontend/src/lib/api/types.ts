/** Wire types mirroring backend/src/retinue/api/schemas.py.
 * scripts/gen_ts_client.py regenerates a full client from OpenAPI (D24);
 * until that lands in CI these are maintained by hand and covered by e2e. */

export interface User {
  id: string;
  email: string;
  name: string | null;
  role: "owner" | "admin" | "member" | "viewer";
  settings: Record<string, unknown>;
  created_at: number;
}

export interface TokenResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
  refresh_token: string | null;
}

export interface Conversation {
  id: string;
  title: string | null;
  agent_id: string | null;
  agent_version_id: string | null;
  forked_from_message_id?: string | null;
  is_incognito?: boolean;
  model_override: string | null;
  params_override: Record<string, unknown> | null;
  folder: string | null;
  pinned: boolean;
  is_archived: boolean;
  created_at: number;
  updated_at: number;
  last_message_at: number | null;
}

export interface MessagePart {
  idx: number;
  type: string;
  content: { text?: string } & Record<string, unknown>;
}

export interface Message {
  id: string;
  conversation_id: string;
  parent_id: string | null;
  role: "user" | "assistant" | "system" | "tool";
  status: "streaming" | "complete" | "stopped" | "error";
  model: string | null;
  agent_version_id?: string | null;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: number;
  parts: MessagePart[];
  attachments?: { file_id: string; kind: string; name: string | null; mime: string | null }[];
}

export interface MessageList {
  conversation_id: string;
  messages: Message[];
}

export interface ModelInfo {
  id: string;
  provider: string;
  display_name: string;
  context_window: number;
  max_output_tokens: number;
  supports_vision: boolean;
  supports_tools: boolean;
  input_cost_per_mtok: number | null;
  output_cost_per_mtok: number | null;
}

export interface Credential {
  id: string;
  provider: string;
  label: string | null;
  base_url: string | null;
  org: boolean;
  key_hint: string;
  created_at: number;
}

export interface ApiKey {
  id: string;
  name: string;
  scopes: string[];
  last_used_at: number | null;
  expires_at: number | null;
  created_at: number;
  key?: string; // present only in the create response
}

export interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  cost_usd: number;
  messages: number;
}

export interface UsageSummary {
  days: number;
  totals: UsageTotals;
  by_model: (UsageTotals & { model: string })[];
  by_day: (UsageTotals & { day: string })[];
}

export interface ErrorEnvelope {
  error: { code: string; message: string; retryable: boolean; details: Record<string, unknown> };
}

export interface ChatSendPayload {
  message_id: string;
  conversation_id?: string;
  text?: string;
  model?: string;
  agent_id?: string;
  file_ids?: string[];
}

// -- v0.2+ feature types --------------------------------------------------------

export interface AgentVersion {
  id: string;
  version: number;
  system_prompt: string;
  model: string;
  params: Record<string, unknown>;
  tools: { type: string; ref: string; config?: { mode?: string } }[];
  mcp_servers: { server_id: string; tool_allowlist?: string[] }[];
  collection_ids: string[];
  starters: string[];
  changelog: string | null;
  created_at: number;
}

export interface Agent {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  avatar: Record<string, unknown> | null;
  visibility: "private" | "org" | "public";
  is_archived: boolean;
  owned: boolean;
  current_version: AgentVersion | null;
  created_at: number;
  updated_at: number;
}

export interface AgentBehaviorPayload {
  system_prompt: string;
  model: string;
  params?: Record<string, unknown>;
  tools?: { type: string; ref: string; config?: { mode?: string } }[];
  mcp_servers?: { server_id: string }[];
  collection_ids?: string[];
  starters?: string[];
  changelog?: string | null;
}

export interface FileInfo {
  id: string;
  original_name: string;
  mime: string;
  size: number;
  status: "uploading" | "ready" | "failed";
  meta: Record<string, unknown>;
  created_at: number;
}

export interface UploadSessionInfo {
  file_id: string;
  upload_id: string | null;
  chunk_size: number;
  expires_at: number;
  already_exists: boolean;
}

export interface Collection {
  id: string;
  name: string;
  description: string | null;
  embed_model: string;
  embed_dim: number;
  visibility: string;
  created_at: number;
}

export interface CollectionStatus {
  collection_id: string;
  files: { file_id: string; name: string; status: string; chunks: number }[];
}

export interface Memory {
  id: string;
  content: string;
  status: "proposed" | "active" | "disabled";
  source_conversation_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface SearchHit {
  kind: "message" | "conversation" | "file" | "agent";
  id: string;
  conversation_id: string | null;
  title: string | null;
  snippet: string;
  created_at: number | null;
}

export interface Share {
  id: string;
  token: string;
  url: string;
  mode: string;
  expires_at: number | null;
  created_at: number;
}

export interface SharedThread {
  title: string | null;
  created_at: number;
  messages: Message[];
}

export interface McpServer {
  id: string;
  name: string;
  transport: "stdio" | "http";
  spec: Record<string, unknown>;
  has_secrets: boolean;
  enabled: boolean;
  org: boolean;
  last_status: { ok?: boolean; tools?: number; error?: string } | null;
  created_at: number;
}

export interface ActionInfo {
  id: string;
  name: string;
  operations: { name: string; method: string; path: string; summary: string }[];
  host_allowlist: string[];
  auth_type: string;
  created_at: number;
}

export interface AdminUser {
  id: string;
  email: string;
  name: string | null;
  role: "owner" | "admin" | "member" | "viewer";
  is_active: boolean;
  created_at: number;
}

export interface JobInfo {
  id: string;
  type: string;
  status: string;
  priority: number;
  attempts: number;
  last_error: string | null;
  run_at: number;
  created_at: number;
  finished_at: number | null;
}

export interface AuditEntry {
  id: string;
  actor_id: string | null;
  action: string;
  target: string | null;
  meta: Record<string, unknown>;
  ip: string | null;
  created_at: number;
}

export interface PreflightReport {
  agent_id: string;
  ok: boolean;
  items: { check: string; ok: boolean; detail: string }[];
}

// -- data sources & connectors (§30, §28.6) -----------------------------------------

export interface EngineConfigField {
  name: string;
  required: boolean;
  default: unknown;
  hint: string;
}

export interface EngineCatalogEntry {
  key: string;
  label: string;
  category: string;
  dialect: string | null;
  query_language: string;
  default_port: number | null;
  config_fields: EngineConfigField[];
  secret_fields: string[];
  file_based: boolean;
  available: boolean;
  install_extra: string;
  notes: string;
}

export interface DataSource {
  id: string;
  name: string;
  engine: string;
  engine_label: string;
  config: Record<string, unknown>;
  has_secrets: boolean;
  policy: Record<string, unknown>;
  status: "unverified" | "ok" | "failed";
  last_test: { ok: boolean; stages: TestStage[]; at?: number } | null;
  created_at: number;
}

export interface TestStage {
  stage: string;
  ok: boolean;
  latency_ms: number;
  detail: string;
}

export interface QueryResultData {
  columns: string[];
  rows: unknown[][];
  row_count: number;
  truncated: boolean;
  elapsed_ms: number;
  note: string | null;
}

export interface ConnectorEntry {
  key: string;
  name: string;
  category: string;
  kind: "mcp-stdio" | "mcp-http" | "openapi";
  description: string;
  secrets: { name: string; label: string; required: boolean }[];
  params: { name: string; label: string; required: boolean; default: string }[];
  runtime: string;
  docs: string;
}
