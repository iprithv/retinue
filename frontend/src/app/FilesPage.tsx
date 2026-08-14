/** Files & knowledge collections (§11, §10): resumable uploads with dedupe,
 * extraction status, and collection indexing. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  Download,
  FileText,
  Loader2,
  Plus,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { useRef, useState } from "react";
import { Button, EmptyState, Input, Spinner } from "../components/ui";
import { api } from "../lib/api/client";
import type { Collection, FileInfo } from "../lib/api/types";
import {
  featureKeys,
  useCollections,
  useCollectionStatus,
  useFiles,
} from "../lib/queries";
import { uploadFile } from "../lib/upload";
import { useAuth } from "../stores/auth";

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function CollectionPanel({
  collection,
  selectedFiles,
  onClearSelection,
}: {
  collection: Collection;
  selectedFiles: string[];
  onClearSelection: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: status } = useCollectionStatus(
    collection.id,
    true, // poll while indexing is likely
  );
  const addFiles = useMutation({
    mutationFn: () =>
      api(`/api/collections/${collection.id}/files`, {
        method: "POST",
        body: { file_ids: selectedFiles },
      }),
    onSuccess: () => {
      onClearSelection();
      void queryClient.invalidateQueries({
        queryKey: featureKeys.collectionStatus(collection.id),
      });
    },
  });
  const removeCollection = useMutation({
    mutationFn: () => api(`/api/collections/${collection.id}`, { method: "DELETE" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: featureKeys.collections }),
  });

  return (
    <div className="rounded-xl border border-line bg-surface-2 p-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Database className="size-3.5 text-accent" />
          {collection.name}
        </div>
        <div className="flex items-center gap-1.5">
          {selectedFiles.length > 0 ? (
            <Button
              variant="outline"
              className="!px-2 !py-1 text-xs"
              onClick={() => addFiles.mutate()}
              disabled={addFiles.isPending}
            >
              <Plus className="size-3" /> add {selectedFiles.length} selected
            </Button>
          ) : null}
          <Button
            variant="danger"
            className="!px-1.5 !py-1"
            onClick={() => {
              if (confirm(`Delete collection "${collection.name}"?`)) {
                removeCollection.mutate();
              }
            }}
          >
            <Trash2 className="size-3" />
          </Button>
        </div>
      </div>
      <div className="mt-1 text-[11px] text-muted">
        {collection.embed_model}
        {collection.embed_dim ? ` · dim ${collection.embed_dim}` : ""}
      </div>
      {status?.files.length ? (
        <ul className="mt-2 space-y-1">
          {status.files.map((f) => (
            <li key={f.file_id} className="flex items-center justify-between text-xs">
              <span className="truncate">{f.name}</span>
              <span
                className={
                  f.status === "indexed"
                    ? "text-success"
                    : f.status === "failed"
                      ? "text-danger"
                      : "text-muted"
                }
              >
                {f.status === "indexed" ? `${f.chunks} chunks` : f.status}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-2 text-xs text-muted">
          Select files on the left, then add them here to index.
        </div>
      )}
    </div>
  );
}

export function FilesPage() {
  const queryClient = useQueryClient();
  const { data: files, isLoading } = useFiles();
  const { data: collections } = useCollections();
  const [selected, setSelected] = useState<string[]>([]);
  const [uploading, setUploading] = useState<{ name: string; progress: number }[]>([]);
  const [newCollection, setNewCollection] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: featureKeys.files });

  const handleFiles = (list: FileList | File[]) => {
    for (const file of Array.from(list)) {
      const entry = { name: file.name, progress: 0 };
      setUploading((u) => [...u, entry]);
      void uploadFile(file, ({ sent, total }) => {
        entry.progress = sent / total;
        setUploading((u) => [...u]);
      })
        .catch(() => {})
        .finally(() => {
          setUploading((u) => u.filter((x) => x !== entry));
          refresh();
        });
    }
  };

  const removeFile = async (file: FileInfo) => {
    await api(`/api/files/${file.id}`, { method: "DELETE" });
    setSelected((s) => s.filter((id) => id !== file.id));
    refresh();
  };

  const download = (file: FileInfo) => {
    const token = useAuth.getState().accessToken;
    void fetch(`/api/files/${file.id}/content`, {
      headers: token ? { authorization: `Bearer ${token}` } : {},
    })
      .then((r) => r.blob())
      .then((blob) => {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = file.original_name;
        a.click();
        URL.revokeObjectURL(a.href);
      });
  };

  const createCollection = async () => {
    const name = newCollection.trim();
    if (!name) return;
    setNewCollection("");
    await api("/api/collections", { method: "POST", body: { name } });
    void queryClient.invalidateQueries({ queryKey: featureKeys.collections });
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-line bg-surface px-6 py-3">
        <h1 className="text-sm font-semibold">Files & knowledge</h1>
        <Button onClick={() => inputRef.current?.click()}>
          <UploadCloud className="size-3.5" /> Upload
        </Button>
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) handleFiles(e.target.files);
            e.target.value = "";
          }}
        />
      </header>
      <div
        className="flex min-h-0 flex-1"
        onDrop={(e) => {
          e.preventDefault();
          handleFiles(e.dataTransfer.files);
        }}
        onDragOver={(e) => e.preventDefault()}
      >
        {/* files table */}
        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          {uploading.map((u, i) => (
            <div
              key={i}
              className="mb-2 flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm"
            >
              <Loader2 className="size-3.5 animate-spin text-muted" />
              <span className="flex-1 truncate">{u.name}</span>
              <span className="text-xs text-muted">{Math.round(u.progress * 100)}%</span>
            </div>
          ))}
          {isLoading ? (
            <div className="flex justify-center pt-12">
              <Spinner />
            </div>
          ) : files?.length ? (
            <table className="w-full max-w-3xl text-sm">
              <thead>
                <tr className="text-left text-[11px] tracking-wider text-muted uppercase">
                  <th className="w-8 pb-2"></th>
                  <th className="pb-2">Name</th>
                  <th className="pb-2">Size</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2"></th>
                </tr>
              </thead>
              <tbody>
                {files.map((file) => (
                  <tr key={file.id} className="group border-t border-line">
                    <td className="py-2">
                      <input
                        type="checkbox"
                        checked={selected.includes(file.id)}
                        onChange={(e) =>
                          setSelected((s) =>
                            e.target.checked
                              ? [...s, file.id]
                              : s.filter((id) => id !== file.id),
                          )
                        }
                      />
                    </td>
                    <td className="max-w-64 py-2">
                      <div className="flex items-center gap-2">
                        <FileText className="size-3.5 shrink-0 text-muted" />
                        <span className="truncate" title={file.original_name}>
                          {file.original_name}
                        </span>
                      </div>
                      <div className="text-[10px] text-muted">{file.mime}</div>
                    </td>
                    <td className="py-2 text-xs text-muted">{bytes(file.size)}</td>
                    <td className="py-2 text-xs">
                      {file.status === "ready" ? (
                        typeof file.meta.text_chars === "number" ? (
                          <span className="text-success">
                            extracted ({Math.round((file.meta.text_chars as number) / 1000)}k chars)
                          </span>
                        ) : file.meta.extraction ? (
                          <span className="text-muted" title={String(file.meta.extraction)}>
                            no text
                          </span>
                        ) : (
                          <span className="text-muted">processing…</span>
                        )
                      ) : (
                        <span className="text-warn">{file.status}</span>
                      )}
                    </td>
                    <td className="py-2">
                      <div className="flex justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                        <button
                          className="rounded p-1 text-muted hover:bg-surface-3 hover:text-ink"
                          onClick={() => download(file)}
                          title="Download"
                        >
                          <Download className="size-3.5" />
                        </button>
                        <button
                          className="rounded p-1 text-muted hover:bg-surface-3 hover:text-danger"
                          onClick={() => void removeFile(file)}
                          title="Delete"
                        >
                          <Trash2 className="size-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <EmptyState
              title="No files yet"
              hint="Drop files anywhere on this page. Identical files are stored once (BLAKE3 dedupe)."
            />
          )}
        </div>

        {/* collections rail */}
        <aside className="w-80 shrink-0 space-y-3 overflow-y-auto border-l border-line p-4">
          <h2 className="text-xs font-semibold tracking-wider text-muted uppercase">
            Collections
          </h2>
          <div className="flex gap-1.5">
            <Input
              placeholder="New collection…"
              value={newCollection}
              onChange={(e) => setNewCollection(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void createCollection()}
            />
            <Button onClick={() => void createCollection()} disabled={!newCollection.trim()}>
              <Plus className="size-3.5" />
            </Button>
          </div>
          {(collections ?? []).map((collection) => (
            <CollectionPanel
              key={collection.id}
              collection={collection}
              selectedFiles={selected}
              onClearSelection={() => setSelected([])}
            />
          ))}
        </aside>
      </div>
    </div>
  );
}
