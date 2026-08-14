/** Memory viewer (§14): explicit, inspectable, "why does it know this?". */
import { useQueryClient } from "@tanstack/react-query";
import { Brain, Check, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api/client";
import { featureKeys, useMemories, usePatchMe } from "../../lib/queries";
import { useAuth } from "../../stores/auth";

export function MemorySection() {
  const queryClient = useQueryClient();
  const { data: memories } = useMemories();
  const [draft, setDraft] = useState("");
  const patchMe = usePatchMe();
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const mode = (user?.settings?.["memory_mode"] as string | undefined) ?? "review";

  const refresh = () => void queryClient.invalidateQueries({ queryKey: featureKeys.memories });

  const add = async () => {
    const content = draft.trim();
    if (!content) return;
    setDraft("");
    await api("/api/memories", { method: "POST", body: { content } });
    refresh();
  };

  const setMode = (value: string) =>
    patchMe.mutate({ settings: { memory_mode: value } }, { onSuccess: (u) => setUser(u) });

  const proposed = (memories ?? []).filter((m) => m.status === "proposed");
  const rest = (memories ?? []).filter((m) => m.status !== "proposed");

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Brain className="size-4" /> Memory
      </h2>
      <p className="text-sm text-muted">
        Facts Retinue may recall across conversations. Extraction mode:{" "}
        <select
          className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-xs"
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="review">review (propose, I approve)</option>
          <option value="auto">auto</option>
          <option value="off">off</option>
        </select>
      </p>

      {proposed.length > 0 ? (
        <div className="space-y-1.5 rounded-xl border border-warn/40 bg-warn/5 p-3">
          <div className="text-xs font-semibold text-warn">Proposed — approve or reject</div>
          {proposed.map((memory) => (
            <div key={memory.id} className="flex items-center gap-2 text-sm">
              <span className="flex-1">{memory.content}</span>
              <button
                className="rounded p-1 text-success hover:bg-surface-3"
                onClick={() =>
                  void api(`/api/memories/${memory.id}/approve`, {
                    method: "POST",
                    body: {},
                  }).then(refresh)
                }
              >
                <Check className="size-3.5" />
              </button>
              <button
                className="rounded p-1 text-danger hover:bg-surface-3"
                onClick={() =>
                  void api(`/api/memories/${memory.id}`, { method: "DELETE" }).then(refresh)
                }
              >
                <X className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      ) : null}

      <div className="flex gap-1.5">
        <Input
          placeholder="Add a memory (e.g. 'I prefer concise answers')"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void add()}
        />
        <Button onClick={() => void add()} disabled={!draft.trim()}>
          <Plus className="size-3.5" />
        </Button>
      </div>

      <ul className="space-y-1.5">
        {rest.map((memory) => (
          <li
            key={memory.id}
            className="flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm"
          >
            <span className={`flex-1 ${memory.status === "disabled" ? "text-muted line-through" : ""}`}>
              {memory.content}
            </span>
            <button
              className="text-xs text-muted hover:text-ink"
              onClick={() =>
                void api(`/api/memories/${memory.id}`, {
                  method: "PATCH",
                  body: { status: memory.status === "disabled" ? "active" : "disabled" },
                }).then(refresh)
              }
            >
              {memory.status === "disabled" ? "enable" : "disable"}
            </button>
            <button
              className="rounded p-1 text-muted hover:bg-surface-3 hover:text-danger"
              onClick={() =>
                void api(`/api/memories/${memory.id}`, { method: "DELETE" }).then(refresh)
              }
            >
              <Trash2 className="size-3.5" />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
