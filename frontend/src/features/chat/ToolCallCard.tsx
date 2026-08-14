/** Tool-call cards (§6.6): collapsed name+status, expanded args/result. */
import { CheckCircle2, ChevronRight, CircleAlert, Clock, Wrench, XCircle } from "lucide-react";
import { useState } from "react";

export interface ToolCallData {
  call_id?: string;
  name?: string;
  args?: Record<string, unknown>;
}

export interface ToolResultData {
  call_id?: string;
  status?: string;
  summary?: string;
}

function StatusIcon({ status }: { status: string | undefined }) {
  if (status === "ok") return <CheckCircle2 className="size-3.5 text-success" />;
  if (status === "denied") return <XCircle className="size-3.5 text-danger" />;
  if (status === "timeout") return <Clock className="size-3.5 text-warn" />;
  if (status === "error") return <CircleAlert className="size-3.5 text-danger" />;
  return <Clock className="size-3.5 animate-pulse text-muted" />;
}

export function ToolCallCard({
  call,
  result,
}: {
  call: ToolCallData;
  result: ToolResultData | undefined;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2 overflow-hidden rounded-xl border border-line bg-surface-2 text-sm">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-surface-3"
        onClick={() => setOpen(!open)}
      >
        <ChevronRight className={`size-3.5 text-muted transition-transform ${open ? "rotate-90" : ""}`} />
        <Wrench className="size-3.5 text-muted" />
        <span className="font-mono text-xs">{call.name ?? "tool"}</span>
        <span className="ml-auto flex items-center gap-1 text-xs text-muted">
          <StatusIcon status={result?.status} />
          {result?.status ?? "running"}
        </span>
      </button>
      {open ? (
        <div className="border-t border-line px-3 py-2 text-xs">
          <div className="mb-1 font-semibold text-muted">arguments</div>
          <pre className="mb-2 overflow-x-auto rounded bg-surface p-2">
            {JSON.stringify(call.args ?? {}, null, 2)}
          </pre>
          {result ? (
            <>
              <div className="mb-1 font-semibold text-muted">result</div>
              <pre className="max-h-64 overflow-auto rounded bg-surface p-2 whitespace-pre-wrap">
                {result.summary ?? "(empty)"}
              </pre>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function CitationChips({
  citations,
}: {
  citations: { n?: number; file_id?: string; file_name?: string }[];
}) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {citations.map((c, i) => (
        <span
          key={`${c.file_id}-${i}`}
          className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[11px] text-muted"
          title={c.file_name ?? undefined}
        >
          <span className="font-semibold text-accent">[{c.n}]</span>
          <span className="max-w-40 truncate">{c.file_name}</span>
        </span>
      ))}
    </div>
  );
}
