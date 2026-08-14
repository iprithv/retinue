/** Agent Studio (§6.6): config left, live test chat right, unsaved-diff
 * indicator, version history with one-click revert, export, preflight.
 * Saving always creates version N+1 (§9.1). */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  History,
  Play,
  RotateCcw,
  Save,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Input, Spinner } from "../components/ui";
import { api } from "../lib/api/client";
import type { AgentBehaviorPayload, AgentVersion, PreflightReport } from "../lib/api/types";
import { Markdown } from "../lib/markdown/render";
import {
  featureKeys,
  useAgent,
  useAgentVersions,
  useCollections,
  useMcpServers,
  useModels,
  useSaveAgentVersion,
} from "../lib/queries";
import { postSSE } from "../lib/sse";
import { useAuth } from "../stores/auth";

const BUILTIN_TOOLS = [
  { ref: "web_search", label: "Web search" },
  { ref: "web_fetch", label: "Fetch web pages" },
  { ref: "file_read", label: "Read files" },
  { ref: "file_search", label: "Search files" },
  { ref: "db_sources", label: "List data sources" },
  { ref: "db_schema", label: "Inspect DB schemas" },
  { ref: "db_query", label: "Query databases (read-only)" },
  { ref: "db_sample", label: "Sample DB tables" },
  { ref: "image_gen", label: "Generate images" },
  { ref: "code_exec", label: "Run code (sandbox)" },
];

interface Draft {
  name: string;
  description: string;
  system_prompt: string;
  model: string;
  temperature: string;
  tools: Record<string, "auto" | "ask_user" | "off">;
  mcp_server_ids: string[];
  collection_ids: string[];
  starters: string;
}

function draftFrom(name: string, description: string | null, v: AgentVersion | null): Draft {
  const tools: Draft["tools"] = {};
  for (const t of BUILTIN_TOOLS) tools[t.ref] = "off";
  for (const t of v?.tools ?? []) {
    if (t.type === "builtin") {
      tools[t.ref] = (t.config?.mode as "auto" | "ask_user" | undefined) ?? "auto";
    }
  }
  return {
    name,
    description: description ?? "",
    system_prompt: v?.system_prompt ?? "",
    model: v?.model ?? "",
    temperature:
      v?.params && typeof v.params.temperature === "number" ? String(v.params.temperature) : "",
    tools,
    mcp_server_ids: (v?.mcp_servers ?? []).map((m) => m.server_id),
    collection_ids: v?.collection_ids ?? [],
    starters: (v?.starters ?? []).join("\n"),
  };
}

function behaviorFrom(draft: Draft): AgentBehaviorPayload {
  const params: Record<string, unknown> = {};
  const temperature = parseFloat(draft.temperature);
  if (!Number.isNaN(temperature)) params.temperature = temperature;
  return {
    system_prompt: draft.system_prompt,
    model: draft.model,
    params,
    tools: Object.entries(draft.tools)
      .filter(([, mode]) => mode !== "off")
      .map(([ref, mode]) => ({ type: "builtin", ref, config: { mode } })),
    mcp_servers: draft.mcp_server_ids.map((server_id) => ({ server_id })),
    collection_ids: draft.collection_ids,
    starters: draft.starters
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
  };
}

interface BenchTurn {
  role: "user" | "assistant";
  text: string;
  error?: string;
}

function TestBench({ agentId, draft }: { agentId: string; draft: Draft }) {
  const [turns, setTurns] = useState<BenchTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns]);

  const run = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    const history = [...turns, { role: "user" as const, text }];
    setTurns([...history, { role: "assistant", text: "" }]);
    setBusy(true);
    try {
      await postSSE(
        `/api/agents/${agentId}/test`,
        {
          messages: history.map((t) => ({ role: t.role, content: t.text })),
          behavior: behaviorFrom(draft),
        },
        {
          accessToken: useAuth.getState().accessToken,
          signal: new AbortController().signal,
          onEvent: (event) => {
            if (event.event === "delta") {
              const delta = event.data.text as string;
              setTurns((prev) => {
                const next = [...prev];
                const last = next[next.length - 1]!;
                next[next.length - 1] = { ...last, text: last.text + delta };
                return next;
              });
            } else if (event.event === "error") {
              const message = event.data.message as string;
              setTurns((prev) => {
                const next = [...prev];
                const last = next[next.length - 1]!;
                next[next.length - 1] = { ...last, error: message };
                return next;
              });
            }
          },
        },
      );
    } catch (error) {
      setTurns((prev) => {
        const next = [...prev];
        const last = next[next.length - 1]!;
        next[next.length - 1] = {
          ...last,
          error: error instanceof Error ? error.message : "test failed",
        };
        return next;
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-line px-4 py-2">
        <span className="text-xs font-semibold tracking-wider text-muted uppercase">
          Test bench (unsaved config, nothing persisted)
        </span>
        <Button variant="ghost" className="!px-2 !py-1 text-xs" onClick={() => setTurns([])}>
          clear
        </Button>
      </div>
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {turns.map((turn, i) =>
          turn.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent/10 px-3 py-2 text-sm whitespace-pre-wrap">
                {turn.text}
              </div>
            </div>
          ) : (
            <div key={i} className="text-sm">
              {turn.text ? <Markdown text={turn.text} /> : busy && i === turns.length - 1 ? (
                <span className="text-muted">…</span>
              ) : null}
              {turn.error ? (
                <div className="mt-1 rounded border border-danger/30 bg-danger/5 px-2 py-1 text-xs text-danger">
                  {turn.error}
                </div>
              ) : null}
            </div>
          ),
        )}
        {turns.length === 0 ? (
          <div className="pt-8 text-center text-xs text-muted">
            Try the agent with the config on the left — edits apply instantly.
          </div>
        ) : null}
      </div>
      <div className="flex gap-2 border-t border-line p-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
          placeholder="Test a prompt…"
          className="flex-1 rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-sm outline-none focus:border-accent/60"
        />
        <Button onClick={() => void run()} disabled={busy || !input.trim()}>
          <Play className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}

export function AgentStudioPage() {
  const { agentId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: agent, isLoading } = useAgent(agentId);
  const { data: versions } = useAgentVersions(agentId);
  const { data: models } = useModels();
  const { data: collections } = useCollections();
  const { data: mcpServers } = useMcpServers();
  const save = useSaveAgentVersion(agentId ?? "");

  const [draft, setDraft] = useState<Draft | null>(null);
  const [showVersions, setShowVersions] = useState(false);
  const [preflight, setPreflight] = useState<PreflightReport | null>(null);

  // initialize the draft from freshly loaded data during render (the React
  // "adjusting state while rendering" pattern) — no effect, no extra paint
  if (agent && draft === null) {
    setDraft(draftFrom(agent.name, agent.description, agent.current_version));
  }

  const baseline = useMemo(
    () =>
      agent ? JSON.stringify(draftFrom(agent.name, agent.description, agent.current_version)) : "",
    [agent],
  );
  const dirty = draft !== null && JSON.stringify(draft) !== baseline;

  const revert = useMutation({
    mutationFn: (version: number) =>
      api(`/api/agents/${agentId}/revert/${version}`, { method: "POST", body: {} }),
    onSuccess: () => {
      setDraft(null); // reload from the new current version
      void queryClient.invalidateQueries({ queryKey: featureKeys.agent(agentId ?? "") });
      void queryClient.invalidateQueries({
        queryKey: featureKeys.agentVersions(agentId ?? ""),
      });
    },
  });

  if (isLoading || !agent || !draft) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const patch = (updates: Partial<Draft>) => setDraft({ ...draft, ...updates });

  const persist = async () => {
    if (draft.name !== agent.name || draft.description !== (agent.description ?? "")) {
      await api(`/api/agents/${agent.id}`, {
        method: "PATCH",
        body: { name: draft.name, description: draft.description || null },
      });
    }
    save.mutate(behaviorFrom(draft), {
      onSuccess: () => {
        void queryClient.invalidateQueries({ queryKey: featureKeys.agent(agent.id) });
      },
    });
  };

  const runPreflight = () =>
    void api<PreflightReport>(`/api/agents/${agent.id}/preflight`, {
      method: "POST",
      body: {},
    }).then(setPreflight);

  const remove = async () => {
    if (!confirm(`Delete agent "${agent.name}"?`)) return;
    await api(`/api/agents/${agent.id}`, { method: "DELETE" });
    void queryClient.invalidateQueries({ queryKey: featureKeys.agents });
    navigate("/agents");
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5">
        <div className="flex items-center gap-2 text-sm">
          <button className="text-muted hover:text-ink" onClick={() => navigate("/agents")}>
            Agents
          </button>
          <span className="text-muted">/</span>
          <span className="font-medium">{agent.name}</span>
          <span className="rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">
            v{agent.current_version?.version ?? "?"}
          </span>
          {dirty ? (
            <span className="rounded-full bg-warn/10 px-2 py-0.5 text-[10px] text-warn">
              unsaved changes
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" onClick={runPreflight} title="Preflight (§30.7)">
            <ShieldCheck className="size-3.5" /> Preflight
          </Button>
          <Button variant="ghost" onClick={() => setShowVersions(!showVersions)}>
            <History className="size-3.5" /> Versions
          </Button>
          <Button variant="danger" onClick={() => void remove()}>
            <Trash2 className="size-3.5" />
          </Button>
          <Button onClick={() => void persist()} disabled={!dirty || save.isPending}>
            <Save className="size-3.5" /> Save as v
            {(agent.current_version?.version ?? 0) + 1}
          </Button>
        </div>
      </header>

      {preflight ? (
        <div className="border-b border-line bg-surface-2 px-4 py-2 text-xs">
          <div className="flex items-center gap-2">
            <span className={preflight.ok ? "text-success" : "text-danger"}>
              preflight {preflight.ok ? "passed" : "found issues"}
            </span>
            <button className="text-muted hover:text-ink" onClick={() => setPreflight(null)}>
              dismiss
            </button>
          </div>
          <div className="mt-1 flex flex-wrap gap-3">
            {preflight.items.map((item, i) => (
              <span key={i} className="inline-flex items-center gap-1">
                {item.ok ? (
                  <Check className="size-3 text-success" />
                ) : (
                  <X className="size-3 text-danger" />
                )}
                <span className="font-medium">{item.check}</span>
                <span className="text-muted">{item.detail}</span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        {/* config column */}
        <div className="min-h-0 w-1/2 overflow-y-auto border-r border-line p-5">
          <div className="mx-auto flex max-w-xl flex-col gap-4">
            <label className="text-xs font-semibold text-muted">
              Name
              <Input
                className="mt-1"
                value={draft.name}
                onChange={(e) => patch({ name: e.target.value })}
              />
            </label>
            <label className="text-xs font-semibold text-muted">
              Description
              <Input
                className="mt-1"
                value={draft.description}
                onChange={(e) => patch({ description: e.target.value })}
              />
            </label>
            <label className="text-xs font-semibold text-muted">
              System prompt
              <textarea
                className="mt-1 min-h-48 w-full rounded-lg border border-line bg-surface-2 p-3 font-mono text-xs focus:border-accent/60 focus:outline-none"
                value={draft.system_prompt}
                onChange={(e) => patch({ system_prompt: e.target.value })}
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="text-xs font-semibold text-muted">
                Model
                <select
                  className="mt-1 w-full rounded-lg border border-line bg-surface-2 px-2 py-1.5 text-sm"
                  value={draft.model}
                  onChange={(e) => patch({ model: e.target.value })}
                >
                  {draft.model && !models?.some((m) => m.id === draft.model) ? (
                    <option value={draft.model}>{draft.model}</option>
                  ) : null}
                  {(models ?? []).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.id}
                    </option>
                  ))}
                </select>
              </label>
              <label className="text-xs font-semibold text-muted">
                Temperature
                <Input
                  className="mt-1"
                  placeholder="provider default"
                  value={draft.temperature}
                  onChange={(e) => patch({ temperature: e.target.value })}
                />
              </label>
            </div>

            <fieldset>
              <legend className="text-xs font-semibold text-muted">Built-in tools</legend>
              <div className="mt-1.5 space-y-1.5">
                {BUILTIN_TOOLS.map((tool) => (
                  <div
                    key={tool.ref}
                    className="flex items-center justify-between rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-sm"
                  >
                    <span>{tool.label}</span>
                    <select
                      className="rounded border border-line bg-surface px-1.5 py-0.5 text-xs"
                      value={draft.tools[tool.ref]}
                      onChange={(e) =>
                        patch({
                          tools: {
                            ...draft.tools,
                            [tool.ref]: e.target.value as "auto" | "ask_user" | "off",
                          },
                        })
                      }
                    >
                      <option value="off">off</option>
                      <option value="auto">auto</option>
                      <option value="ask_user">ask first</option>
                    </select>
                  </div>
                ))}
              </div>
            </fieldset>

            {mcpServers?.length ? (
              <fieldset>
                <legend className="text-xs font-semibold text-muted">MCP servers</legend>
                <div className="mt-1.5 space-y-1.5">
                  {mcpServers.map((server) => (
                    <label
                      key={server.id}
                      className="flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={draft.mcp_server_ids.includes(server.id)}
                        onChange={(e) =>
                          patch({
                            mcp_server_ids: e.target.checked
                              ? [...draft.mcp_server_ids, server.id]
                              : draft.mcp_server_ids.filter((id) => id !== server.id),
                          })
                        }
                      />
                      {server.name}
                      <span className="text-xs text-muted">({server.transport})</span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}

            {collections?.length ? (
              <fieldset>
                <legend className="text-xs font-semibold text-muted">
                  Knowledge collections
                </legend>
                <div className="mt-1.5 space-y-1.5">
                  {collections.map((collection) => (
                    <label
                      key={collection.id}
                      className="flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-1.5 text-sm"
                    >
                      <input
                        type="checkbox"
                        checked={draft.collection_ids.includes(collection.id)}
                        onChange={(e) =>
                          patch({
                            collection_ids: e.target.checked
                              ? [...draft.collection_ids, collection.id]
                              : draft.collection_ids.filter((id) => id !== collection.id),
                          })
                        }
                      />
                      {collection.name}
                    </label>
                  ))}
                </div>
              </fieldset>
            ) : null}

            <label className="text-xs font-semibold text-muted">
              Conversation starters (one per line)
              <textarea
                className="mt-1 min-h-16 w-full rounded-lg border border-line bg-surface-2 p-2 text-sm focus:border-accent/60 focus:outline-none"
                value={draft.starters}
                onChange={(e) => patch({ starters: e.target.value })}
              />
            </label>
          </div>
        </div>

        {/* right column: versions or test bench */}
        <div className="min-h-0 w-1/2">
          {showVersions ? (
            <div className="h-full overflow-y-auto p-4">
              <h3 className="mb-3 text-xs font-semibold tracking-wider text-muted uppercase">
                Version history
              </h3>
              <div className="space-y-2">
                {(versions ?? []).map((version) => (
                  <div
                    key={version.id}
                    className={`rounded-xl border p-3 text-sm ${
                      version.id === agent.current_version?.id
                        ? "border-accent/40 bg-accent/5"
                        : "border-line bg-surface-2"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium">
                        v{version.version}
                        {version.id === agent.current_version?.id ? " · current" : ""}
                      </span>
                      <div className="flex items-center gap-2 text-xs text-muted">
                        {new Date(version.created_at).toLocaleString()}
                        {version.id !== agent.current_version?.id ? (
                          <Button
                            variant="outline"
                            className="!px-2 !py-0.5 text-xs"
                            onClick={() => revert.mutate(version.version)}
                          >
                            <RotateCcw className="size-3" /> revert
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    {version.changelog ? (
                      <div className="mt-1 text-xs text-muted">{version.changelog}</div>
                    ) : null}
                    <div className="mt-1 text-xs text-muted">
                      {version.model} · {version.tools.length} tools ·{" "}
                      {version.collection_ids.length} collections
                    </div>
                    <details className="mt-1 text-xs">
                      <summary className="cursor-pointer text-muted">prompt</summary>
                      <pre className="mt-1 max-h-40 overflow-auto rounded bg-surface p-2 whitespace-pre-wrap">
                        {version.system_prompt}
                      </pre>
                    </details>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <TestBench agentId={agent.id} draft={draft} />
          )}
        </div>
      </div>
    </div>
  );
}
