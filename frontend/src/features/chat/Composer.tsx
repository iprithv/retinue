import { ArrowUp, Loader2, Paperclip, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { FileInfo } from "../../lib/api/types";
import { uploadFile } from "../../lib/upload";
import { useDrafts } from "../../stores/draft";

interface PendingFile {
  key: string;
  name: string;
  progress: number; // 0..1
  info: FileInfo | null;
  error: string | null;
}

export function Composer({
  conversationId,
  streaming,
  onSend,
  onStop,
}: {
  conversationId: string | undefined;
  streaming: boolean;
  onSend: (text: string, fileIds: string[]) => void;
  onStop: () => void;
}) {
  const drafts = useDrafts();
  const value = drafts.get(conversationId);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [pending, setPending] = useState<PendingFile[]>([]);
  const [pendingFor, setPendingFor] = useState(conversationId);
  if (pendingFor !== conversationId) {
    // conversation switched: drop staged attachments (render-adjustment pattern)
    setPendingFor(conversationId);
    setPending([]);
  }

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

  const addFiles = (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      const key = `${file.name}-${Date.now()}-${Math.random()}`;
      setPending((p) => [
        ...p,
        { key, name: file.name, progress: 0, info: null, error: null },
      ]);
      void uploadFile(file, ({ sent, total }) =>
        setPending((p) =>
          p.map((f) => (f.key === key ? { ...f, progress: sent / total } : f)),
        ),
      )
        .then((info) =>
          setPending((p) =>
            p.map((f) => (f.key === key ? { ...f, info, progress: 1 } : f)),
          ),
        )
        .catch((error: Error) =>
          setPending((p) =>
            p.map((f) => (f.key === key ? { ...f, error: error.message } : f)),
          ),
        );
    }
  };

  const uploadsInFlight = pending.some((f) => !f.info && !f.error);

  const submit = () => {
    const text = value.trim();
    if (!text || streaming || uploadsInFlight) return;
    const fileIds = pending.filter((f) => f.info).map((f) => f.info!.id);
    drafts.clear(conversationId);
    setPending([]);
    onSend(text, fileIds);
  };

  return (
    <div className="border-t border-line bg-surface px-4 py-3">
      {pending.length > 0 ? (
        <div className="mx-auto mb-2 flex max-w-3xl flex-wrap gap-1.5">
          {pending.map((f) => (
            <span
              key={f.key}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${
                f.error
                  ? "border-danger/40 text-danger"
                  : "border-line bg-surface-2 text-muted"
              }`}
              title={f.error ?? undefined}
            >
              {!f.info && !f.error ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                <Paperclip className="size-3" />
              )}
              <span className="max-w-48 truncate">{f.name}</span>
              {!f.info && !f.error ? (
                <span>{Math.round(f.progress * 100)}%</span>
              ) : null}
              <button
                onClick={() => setPending((p) => p.filter((x) => x.key !== f.key))}
                className="hover:text-ink"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      ) : null}
      <div className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-line bg-surface-2 p-2 shadow-sm focus-within:border-accent/50">
        <button
          onClick={() => fileInputRef.current?.click()}
          title="Attach files"
          className="flex size-9 items-center justify-center rounded-xl text-muted hover:bg-surface-3 hover:text-ink"
        >
          <Paperclip className="size-4" />
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
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
          onPaste={(e) => {
            const files = Array.from(e.clipboardData.files);
            if (files.length) {
              e.preventDefault();
              addFiles(files);
            }
          }}
          onDrop={(e) => {
            if (e.dataTransfer.files.length) {
              e.preventDefault();
              addFiles(e.dataTransfer.files);
            }
          }}
          onDragOver={(e) => e.preventDefault()}
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
            disabled={!value.trim() || uploadsInFlight}
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
