/** The single subscriber of an in-flight message (§6.3): a token arriving
 * re-renders this component and nothing else. Renders interleaved blocks —
 * text, thinking, tool calls/results, citations — plus approval gates. */
import { ShieldQuestion } from "lucide-react";
import { Button } from "../../components/ui";
import { Markdown } from "../../lib/markdown/render";
import { useStreamingMessage } from "../../stores/streaming";
import { CitationChips, ToolCallCard } from "./ToolCallCard";
import type { ToolCallData, ToolResultData } from "./ToolCallCard";

export function StreamingMessage({
  messageId,
  onApprove,
}: {
  messageId: string;
  onApprove?: (callId: string, ok: boolean) => void;
}) {
  const snapshot = useStreamingMessage(messageId);
  if (!snapshot) return null;

  const results = new Map(
    snapshot.parts
      .filter((p) => p.type === "tool_result")
      .map((p) => [String((p.data as ToolResultData | undefined)?.call_id), p.data as ToolResultData]),
  );
  const citations = snapshot.parts
    .filter((p) => p.type === "citation")
    .map((p) => (p.data ?? {}) as { n?: number; file_id?: string; file_name?: string });

  const renderable = snapshot.parts.filter(
    (p) => p.type === "text" || p.type === "thinking" || p.type === "tool_call",
  );
  const lastText = [...renderable].reverse().find((p) => p.type === "text");

  return (
    <div>
      {renderable.map((part) => {
        if (part.type === "tool_call") {
          const call = (part.data ?? {}) as ToolCallData;
          return (
            <ToolCallCard key={part.index} call={call} result={results.get(String(call.call_id))} />
          );
        }
        if (part.type === "thinking") {
          return (
            <details key={part.index} className="my-1 text-xs text-muted" open>
              <summary className="cursor-pointer select-none">thinking</summary>
              <div className="mt-1 border-l-2 border-line pl-3 whitespace-pre-wrap italic">
                {part.text}
              </div>
            </details>
          );
        }
        return (
          <div
            key={part.index}
            className={
              snapshot.status === "streaming" && part === lastText ? "stream-caret" : undefined
            }
          >
            <Markdown text={part.text} />
          </div>
        );
      })}
      {renderable.length === 0 && snapshot.status === "streaming" ? (
        <div className="stream-caret text-muted" />
      ) : null}

      {snapshot.approvals.map((approval) => (
        <div
          key={approval.call_id}
          className="my-2 rounded-xl border border-warn/40 bg-warn/5 px-3 py-2.5 text-sm"
        >
          <div className="flex items-center gap-2 font-medium">
            <ShieldQuestion className="size-4 text-warn" />
            The agent wants to run <span className="font-mono text-xs">{approval.name}</span>
          </div>
          <pre className="mt-1.5 max-h-32 overflow-auto rounded bg-surface p-2 text-xs">
            {JSON.stringify(approval.args, null, 2)}
          </pre>
          {onApprove ? (
            <div className="mt-2 flex gap-2">
              <Button onClick={() => onApprove(approval.call_id, true)}>Allow</Button>
              <Button variant="outline" onClick={() => onApprove(approval.call_id, false)}>
                Deny
              </Button>
            </div>
          ) : null}
        </div>
      ))}

      <CitationChips citations={citations} />
      {snapshot.error ? (
        <div className="mt-2 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {snapshot.error.message}
        </div>
      ) : null}
    </div>
  );
}
