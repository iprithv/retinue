import { ArrowUp, Square } from "lucide-react";
import { useEffect, useRef } from "react";
import { useDrafts } from "../../stores/draft";

export function Composer({
  conversationId,
  streaming,
  onSend,
  onStop,
}: {
  conversationId: string | undefined;
  streaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}) {
  const drafts = useDrafts();
  const value = drafts.get(conversationId);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // autosize
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  useEffect(() => {
    textareaRef.current?.focus();
  }, [conversationId]);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming) return;
    drafts.clear(conversationId);
    onSend(text);
  };

  return (
    <div className="border-t border-line bg-surface px-4 py-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-line bg-surface-2 p-2 shadow-sm focus-within:border-accent/50">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => drafts.set(conversationId, e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder="Message your retinue…  (Enter to send, Shift+Enter for newline)"
          className="max-h-60 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] outline-none placeholder:text-muted"
        />
        {streaming ? (
          <button
            onClick={onStop}
            title="Stop generating"
            className="flex size-9 items-center justify-center rounded-xl bg-ink text-surface hover:opacity-80"
          >
            <Square className="size-4 fill-current" />
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={!value.trim()}
            title="Send"
            className="flex size-9 items-center justify-center rounded-xl bg-accent text-accent-ink transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            <ArrowUp className="size-4" />
          </button>
        )}
      </div>
    </div>
  );
}
