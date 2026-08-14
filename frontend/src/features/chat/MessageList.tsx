/** Scroll pins to bottom while streaming unless the user scrolls up, then a
 * "jump to latest" pill appears (§6.5). */
import { ArrowDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Message } from "../../lib/api/types";
import { useConversationStreamId, useStreamingMessage } from "../../stores/streaming";
import { MessageItem } from "./MessageItem";
import { StreamingMessage } from "./StreamingMessage";

export function MessageList({
  conversationId,
  messages,
  onRegenerate,
}: {
  conversationId: string;
  messages: Message[];
  onRegenerate: (assistantMessageId: string) => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const atBottomRef = useRef(true);
  const [showJump, setShowJump] = useState(false);

  const streamingId = useConversationStreamId(conversationId);
  const streamingSnapshot = useStreamingMessage(streamingId);
  // completed row may already be in the cache when the stream entry lingers
  const visible = streamingId ? messages.filter((m) => m.id !== streamingId) : messages;
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

  // pin to bottom on new content while the user hasn't scrolled up
  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [messages.length, streamingSnapshot?.version]);

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
              onRegenerate={onRegenerate}
            />
          ))}
          {streamingId ? <StreamingMessage messageId={streamingId} /> : null}
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
