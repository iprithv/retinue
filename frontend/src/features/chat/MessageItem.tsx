import { Check, Copy, RefreshCw } from "lucide-react";
import { memo, useState } from "react";
import type { Message } from "../../lib/api/types";
import { Markdown } from "../../lib/markdown/render";

function textOf(message: Message): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.content.text ?? "")
    .join("\n\n");
}

export const MessageItem = memo(function MessageItem({
  message,
  isLastAssistant,
  onRegenerate,
}: {
  message: Message;
  isLastAssistant: boolean;
  onRegenerate?: (assistantMessageId: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const text = textOf(message);

  const copy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent/10 px-4 py-2.5 whitespace-pre-wrap">
          {text}
        </div>
      </div>
    );
  }

  return (
    <div className="group">
      <Markdown text={text} />
      {message.status === "error" && message.error ? (
        <div className="mt-2 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {message.error.message}
          {message.error.retryable && onRegenerate ? (
            <button
              className="ml-2 underline underline-offset-2"
              onClick={() => onRegenerate(message.id)}
            >
              retry
            </button>
          ) : null}
        </div>
      ) : null}
      {message.status === "stopped" ? (
        <div className="mt-1 text-xs text-muted italic">stopped</div>
      ) : null}
      <div className="mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          className="rounded-md p-1 text-muted hover:bg-surface-3 hover:text-ink"
          onClick={copy}
          title="Copy"
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        </button>
        {isLastAssistant && onRegenerate ? (
          <button
            className="rounded-md p-1 text-muted hover:bg-surface-3 hover:text-ink"
            onClick={() => onRegenerate(message.id)}
            title="Regenerate"
          >
            <RefreshCw className="size-3.5" />
          </button>
        ) : null}
        {message.model ? <span className="ml-1 text-[10px] text-muted">{message.model}</span> : null}
      </div>
    </div>
  );
});
