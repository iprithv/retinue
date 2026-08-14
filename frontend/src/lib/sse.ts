/** SSE over fetch + ReadableStream (D4): POST bodies, AbortController stop,
 * Last-Event-ID resume. Heartbeat comments are swallowed by the parser. */
import { createParser } from "eventsource-parser";
import type { ErrorEnvelope } from "./api/types";
import { ApiError } from "./api/client";

export interface SseMessage {
  id?: number;
  event: string;
  data: Record<string, unknown>;
}

export interface PostSseOptions {
  accessToken: string | null;
  signal: AbortSignal;
  lastEventId?: number;
  onEvent: (message: SseMessage) => void;
}

/** Resolves when the server closes the stream; throws on HTTP or network error. */
export async function postSSE(
  url: string,
  body: unknown,
  options: PostSseOptions,
): Promise<void> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "text/event-stream",
  };
  if (options.accessToken) headers.authorization = `Bearer ${options.accessToken}`;
  if (options.lastEventId && options.lastEventId > 0) {
    headers["last-event-id"] = String(options.lastEventId);
  }

  const response = await fetch(url, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    signal: options.signal,
    credentials: "same-origin",
  });
  if (!response.ok) {
    const envelope = (await response.json().catch(() => null)) as ErrorEnvelope | null;
    throw new ApiError(response.status, envelope?.error ?? null);
  }
  if (!response.body) throw new Error("response has no body stream");

  const parser = createParser({
    onEvent(event) {
      let data: Record<string, unknown> = {};
      try {
        data = event.data ? (JSON.parse(event.data) as Record<string, unknown>) : {};
      } catch {
        return;
      }
      options.onEvent({
        id: event.id ? Number(event.id) : undefined,
        event: event.event ?? "message",
        data,
      });
    },
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.feed(decoder.decode(value, { stream: true }));
  }
}

export function backoffMs(attempt: number): number {
  // 250ms -> 8s with jitter (§6.2 sse client contract)
  const base = Math.min(8000, 250 * 2 ** attempt);
  return base / 2 + Math.random() * (base / 2);
}
