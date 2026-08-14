import { Check, Copy, Paperclip, Pencil, RefreshCw } from "lucide-react";
import { memo, useState } from "react";
import { Button } from "../../components/ui";
import { api } from "../../lib/api/client";
import type { Message } from "../../lib/api/types";
import type { SiblingInfo } from "../../lib/branching";
import { Markdown } from "../../lib/markdown/render";
import { ToolCallCard, CitationChips } from "./ToolCallCard";
import type { ToolCallData, ToolResultData } from "./ToolCallCard";

function textOf(message: Message): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.content.text ?? "")
    .join("\n\n");
}

export function BranchSwitcher({
  info,
  onSelect,
}: {
  info: SiblingInfo;
  onSelect: (childId: string) => void;
}) {
  const prev = info.siblingIds[info.index - 2];
  const next = info.siblingIds[info.index];
  return (
    <span className="inline-flex items-center gap-0.5 text-[11px] text-muted">
      <button
        className="rounded px-1 hover:bg-surface-3 disabled:opacity-30"
        disabled={!prev}
        onClick={() => prev && onSelect(prev)}
      >
        ◂
      </button>
      {info.index}/{info.count}
      <button
        className="rounded px-1 hover:bg-surface-3 disabled:opacity-30"
        disabled={!next}
        onClick={() => next && onSelect(next)}
      >
        ▸
      </button>
    </span>
  );
}

export const MessageItem = memo(function MessageItem({
  message,
  isLastAssistant,
  siblingInfo,
  onRegenerate,
  onSelectBranch,
  onEditSaved,
}: {
  message: Message;
  isLastAssistant: boolean;
  siblingInfo?: SiblingInfo;
  onRegenerate?: (assistantMessageId: string) => void;
  onSelectBranch?: (parentKey: string, childId: string) => void;
  onEditSaved?: (parentKey: string, newMessageId: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const text = textOf(message);

  const copy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  const switcher =
    siblingInfo && onSelectBranch ? (
      <BranchSwitcher
        info={siblingInfo}
        onSelect={(childId) => onSelectBranch(siblingInfo.parentKey, childId)}
      />
    ) : null;

  if (message.role === "user") {
    const saveEdit = async () => {
      const body = draft.trim();
      if (!body || body === text) {
        setEditing(false);
        return;
      }
      const sibling = await api<Message>(`/api/messages/${message.id}`, {
        method: "PATCH",
        body: { text: body },
      });
      setEditing(false);
      onEditSaved?.(message.parent_id ?? "__root__", sibling.id);
    };
    return (
      <div className="group flex flex-col items-end">
        {editing ? (
          <div className="w-full max-w-[85%]">
            <textarea
              className="min-h-24 w-full rounded-xl border border-line bg-surface-2 p-3 text-sm focus:border-accent/60 focus:outline-none"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoFocus
            />
            <div className="mt-1 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditing(false)}>
                Cancel
              </Button>
              <Button onClick={() => void saveEdit()}>Send</Button>
            </div>
          </div>
        ) : (
          <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent/10 px-4 py-2.5 whitespace-pre-wrap">
            {text}
          </div>
        )}
        {message.attachments?.length ? (
          <div className="mt-1 flex flex-wrap justify-end gap-1.5">
            {message.attachments.map((a) => (
              <span
                key={a.file_id}
                className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11px] text-muted"
              >
                <Paperclip className="size-3" /> {a.name ?? a.file_id.slice(0, 8)}
              </span>
            ))}
          </div>
        ) : null}
        <div className="mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
          {switcher}
          {!editing && onEditSaved ? (
            <button
              className="rounded-md p-1 text-muted hover:bg-surface-3 hover:text-ink"
              title="Edit (creates a branch)"
              onClick={() => {
                setDraft(text);
                setEditing(true);
              }}
            >
              <Pencil className="size-3.5" />
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  const citations = message.parts
    .filter((p) => p.type === "citation")
    .map((p) => p.content as { n?: number; file_id?: string; file_name?: string });
  const toolResults = new Map(
    message.parts
      .filter((p) => p.type === "tool_result")
      .map((p) => [String((p.content as ToolResultData).call_id), p.content as ToolResultData]),
  );

  return (
    <div className="group">
      {message.parts
        .filter((p) => p.type === "text" || p.type === "tool_call" || p.type === "thinking")
        .map((part) =>
          part.type === "tool_call" ? (
            <ToolCallCard
              key={part.idx}
              call={part.content as ToolCallData}
              result={toolResults.get(String((part.content as ToolCallData).call_id))}
            />
          ) : part.type === "thinking" ? (
            <details key={part.idx} className="my-1 text-xs text-muted">
              <summary className="cursor-pointer select-none">thinking</summary>
              <div className="mt-1 border-l-2 border-line pl-3 whitespace-pre-wrap italic">
                {part.content.text ?? ""}
              </div>
            </details>
          ) : (
            <Markdown key={part.idx} text={part.content.text ?? ""} />
          ),
        )}
      <CitationChips citations={citations} />
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
        {switcher}
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
