/** Typed fetch wrapper: bearer auth, one silent refresh-and-retry on 401,
 * uniform error envelope (§18) surfaced as ApiError. */
import { refreshAccessToken, useAuth } from "../../stores/auth";
import type { ErrorEnvelope } from "./types";

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  details: Record<string, unknown>;

  constructor(status: number, envelope: ErrorEnvelope["error"] | null) {
    super(envelope?.message ?? `request failed (${status})`);
    this.status = status;
    this.code = envelope?.code ?? "unknown";
    this.retryable = envelope?.retryable ?? false;
    this.details = envelope?.details ?? {};
  }
}

interface ApiOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  retryOn401?: boolean;
}

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { method = "GET", body, retryOn401 = true } = options;
  const token = useAuth.getState().accessToken;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  if (token) headers.authorization = `Bearer ${token}`;

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });

  if (response.status === 401 && retryOn401) {
    const fresh = await refreshAccessToken();
    if (fresh) return api<T>(path, { ...options, retryOn401: false });
  }
  if (!response.ok) {
    const envelope = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    throw new ApiError(response.status, envelope?.error ?? null);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
