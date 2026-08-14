/** Chat send/stop/regenerate orchestration.
 *
 * Reconnect story: the client message id is the idempotency key (§31.4a) — a
 * network drop mid-stream retries the same POST /chat with Last-Event-ID and
 * the server replays the gap losslessly, falling back to a snapshot rebuild
 * or DB replay. Backoff 250ms→8s with jitter.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api/client";
import type { Message, MessageList } from "../../lib/api/types";
import { keys } from "../../lib/queries";
import { backoffMs, postSSE } from "../../lib/sse";
import { uuid7 } from "../../lib/uuid7";
import { useAuth } from "../../stores/auth";
import { streaming } from "../../stores/streaming";

const MAX_RECONNECTS = 4;

interface RunOptions {
  path: string;
  payload: Record<string, unknown>;
  optimisticConversationId: string | undefined;
}

export function useChatController(conversationId: string | undefined) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const abortRef = useRef<AbortController | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  useEffect(() => {
    // leaving the view abandons the subscription; the server's orphan grace
    // plus idempotent re-POST make this safe
    return () => abortRef.current?.abort();
  }, [conversationId]);

  const finalize = useCallback(
    async (finishedConversationId: string, assistantId: string) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.messages(finishedConversationId) }),
        queryClient.invalidateQueries({ queryKey: keys.conversations }),
      ]);
      streaming.clear(assistantId);
    },
    [queryClient],
  );

  const run = useCallback(
    async ({ path, payload, optimisticConversationId }: RunOptions) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setSendError(null);

      let lastEventId = 0;
      let assistantId: string | null = null;
      let liveConversationId = optimisticConversationId;

      for (let attempt = 0; ; attempt++) {
        try {
          await postSSE(path, payload, {
            accessToken: useAuth.getState().accessToken,
            signal: controller.signal,
            lastEventId,
            onEvent: (event) => {
              if (event.id) lastEventId = event.id;
              switch (event.event) {
                case "message_start": {
                  const data = event.data as {
                    message_id: string;
                    conversation_id: string;
                    model: string | null;
                  };
                  assistantId = data.message_id;
                  assistantIdRef.current = assistantId;
                  liveConversationId = data.conversation_id;
                  streaming.start(assistantId, liveConversationId, data.model);
                  if (!optimisticConversationId) {
                    void queryClient.invalidateQueries({ queryKey: keys.conversations });
                    navigate(`/chat/${liveConversationId}`, { replace: false });
                  }
                  break;
                }
                case "block_start": {
                  const data = event.data as { index: number; type: string };
                  if (assistantId) streaming.blockStart(assistantId, data.index, data.type);
                  break;
                }
                case "delta": {
                  const data = event.data as { index: number; text: string };
                  if (assistantId) streaming.appendDelta(assistantId, data.index, data.text);
                  break;
                }
                case "error": {
                  const data = event.data as {
                    code: string;
                    message: string;
                    retryable: boolean;
                  };
                  if (assistantId) streaming.fail(assistantId, data);
                  break;
                }
                case "message_end": {
                  const data = event.data as { stop_reason: string };
                  if (assistantId) {
                    streaming.finish(assistantId, data.stop_reason);
                    if (liveConversationId) void finalize(liveConversationId, assistantId);
                  }
                  break;
                }
                case "resync_required":
                  if (assistantId && liveConversationId) {
                    void finalize(liveConversationId, assistantId);
                  }
                  break;
                default:
                  break; // usage/title/citation: nothing to do in v0.1
              }
            },
          });
          return; // clean end
        } catch (error) {
          if (controller.signal.aborted) return; // user navigated or stopped hard
          if (attempt < MAX_RECONNECTS) {
            // mid-stream network drop: idempotent re-POST resumes the stream
            await new Promise((resolve) => setTimeout(resolve, backoffMs(attempt)));
            if (assistantId) streaming.resetParts(assistantId);
            continue;
          }
          const message = error instanceof Error ? error.message : "connection lost";
          if (assistantId) {
            streaming.fail(assistantId, { code: "network", message, retryable: true });
            streaming.finish(assistantId, "error");
            if (liveConversationId) void finalize(liveConversationId, assistantId);
          } else {
            setSendError(message);
          }
          return;
        }
      }
    },
    [finalize, navigate, queryClient],
  );

  const send = useCallback(
    (text: string, model: string | undefined) => {
      const messageId = uuid7();
      if (conversationId) {
        // optimistic user-message insert (§6.3): identity minted client-side
        const optimistic: Message = {
          id: messageId,
          conversation_id: conversationId,
          parent_id: null,
          role: "user",
          status: "complete",
          model: null,
          error: null,
          created_at: Date.now(),
          parts: [{ idx: 0, type: "text", content: { text } }],
        };
        queryClient.setQueryData<MessageList>(keys.messages(conversationId), (old) =>
          old
            ? { ...old, messages: [...old.messages, optimistic] }
            : { conversation_id: conversationId, messages: [optimistic] },
        );
      }
      void run({
        path: "/api/chat",
        payload: {
          message_id: messageId,
          conversation_id: conversationId,
          text,
          model,
        },
        optimisticConversationId: conversationId,
      });
    },
    [conversationId, queryClient, run],
  );

  const stop = useCallback(() => {
    const assistantId = assistantIdRef.current;
    if (!assistantId) return;
    // graceful stop: the server ends the stream with stop_reason=stopped and
    // the open SSE connection delivers the final events
    void api(`/api/messages/${assistantId}/stop`, { method: "POST", body: {} }).catch(() => {});
  }, []);

  const regenerate = useCallback(
    (assistantMessageId: string, model: string | undefined) => {
      if (!conversationId) return;
      void run({
        path: `/api/messages/${assistantMessageId}/regenerate`,
        payload: { message_id: uuid7(), model },
        optimisticConversationId: conversationId,
      });
    },
    [conversationId, run],
  );

  return { send, stop, regenerate, sendError };
}
