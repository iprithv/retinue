/** Scroll pins to bottom while streaming unless the user scrolls up, then a
 * "jump to latest" pill appears (§6.5). The visible thread is computed
 * client-side from the branch tree (§17). */
import { ArrowDown } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "../../lib/api/types";
import { computeThread } from "../../lib/branching";
import { useBranches } from "../../stores/branch";
import { useConversationStreamId, useStreamingMessage } from "../../stores/streaming";
import { MessageItem } from "./MessageItem";
import { StreamingMessage } from "./StreamingMessage";

export function MessageList({
  conversationId,
  messages,
  onRegenerate,
  onResend,
  onApprove,
}: {
  conversationId: string;
  messages: Message[];
  onRegenerate: (assistantMessageId: string) => void;
  onResend: (userMessageId: string) => void;
  onApprove: (assistantMessageId: string, callId: string, ok: boolean) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const selections = useBranches((s) => s.selections[conversationId]);
  const select = useBranches((s) => s.select);

  const streamingId = useConversationStreamId(conversationId);
  const streamingSnapshot = useStreamingMessage(streamingId);

  const { thread, siblings } = useMemo(
    () => computeThread(messages, selections),
    [messages, selections],
  );
  // completed row may already be in the cache when the stream entry lingers
  const visible = streamingId ? thread.filter((m) => m.id !== streamingId) : thread;
  const lastAssistantId = [...visible].reverse().find((m) => m.role === "assistant")?.id;

  const scrollToBottom = (behavior: ScrollBehavior = "auto") => {
    const el = containerRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior });
  };

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
    atBottomRef.current = nearBottom;
    setShowJump(!nearBottom);
  };

  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [visible.length, streamingSnapshot?.version]);

  useEffect(() => {
    scrollToBottom();
  }, [conversationId]);

  return (
    <div className="relative min-h-0 flex-1">
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="h-full overflow-y-auto px-4 py-6"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-6">
          {visible.map((message) => (
            <MessageItem
              key={message.id}
              message={message}
              isLastAssistant={message.id === lastAssistantId && !streamingId}
              siblingInfo={siblings.get(message.id)}
              onRegenerate={onRegenerate}
              onSelectBranch={(parentKey, childId) =>
                select(conversationId, parentKey, childId)
              }
              onEditSaved={(parentKey, newMessageId) => {
                select(conversationId, parentKey, newMessageId);
                onResend(newMessageId);
              }}
            />
          ))}
          {streamingId ? (
            <StreamingMessage
              messageId={streamingId}
              onApprove={(callId, ok) => onApprove(streamingId, callId, ok)}
            />
          ) : null}
          <div className="h-2" />
        </div>
      </div>
      {showJump ? (
        <button
          onClick={() => {
            scrollToBottom("smooth");
            atBottomRef.current = true;
            setShowJump(false);
          }}
          className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-surface-2 px-3 py-1.5 text-xs shadow-lg hover:bg-surface-3"
        >
          <ArrowDown className="size-3.5" /> latest
        </button>
      ) : null}
    </div>
  );
}
