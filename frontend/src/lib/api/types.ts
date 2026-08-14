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
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: number;
  parts: MessagePart[];
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
}
