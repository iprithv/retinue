/** Agent gallery: your retinue's roster. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, MessageSquare, Plus, Sparkles, Upload } from "lucide-react";
import { useRef } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Button, EmptyState, Spinner } from "../components/ui";
import { api } from "../lib/api/client";
import type { Agent } from "../lib/api/types";
import { featureKeys, useAgents } from "../lib/queries";

export function AgentsPage() {
  const { data: agents, isLoading } = useAgents();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const importRef = useRef<HTMLInputElement | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api<Agent>("/api/agents", {
        method: "POST",
        body: {
          name: "New agent",
          system_prompt: "You are a helpful expert.",
          model: "",
        },
      }),
    onSuccess: (agent) => {
      void queryClient.invalidateQueries({ queryKey: featureKeys.agents });
      navigate(`/agents/${agent.id}`);
    },
  });

  const importAgent = async (file: File) => {
    const payload = JSON.parse(await file.text());
    const agent = await api<Agent>("/api/agents/import", { method: "POST", body: payload });
    void queryClient.invalidateQueries({ queryKey: featureKeys.agents });
    navigate(`/agents/${agent.id}`);
  };

  const createBlank = () => {
    // model left to the studio; backend requires one, so use the first available
    void api<{ id: string }[]>("/api/models").then((models) => {
      const model = models[0]?.id ?? "openai/gpt-4o-mini";
      void api<Agent>("/api/agents", {
        method: "POST",
        body: { name: "New agent", system_prompt: "You are a helpful expert.", model },
      }).then((agent) => {
        void queryClient.invalidateQueries({ queryKey: featureKeys.agents });
        navigate(`/agents/${agent.id}`);
      });
    });
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-line bg-surface px-6 py-3">
        <h1 className="text-sm font-semibold">Your retinue</h1>
        <div className="flex gap-2">
          <input
            ref={importRef}
            type="file"
            accept=".json,.agent.json"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void importAgent(file);
              e.target.value = "";
            }}
          />
          <Button variant="outline" onClick={() => importRef.current?.click()}>
            <Upload className="size-3.5" /> Import
          </Button>
          <Button onClick={createBlank} disabled={create.isPending}>
            <Plus className="size-3.5" /> New agent
          </Button>
        </div>
      </header>
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex justify-center pt-12">
            <Spinner />
          </div>
        ) : agents?.length ? (
          <div className="grid max-w-5xl grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <div
                key={agent.id}
                className="group flex flex-col rounded-2xl border border-line bg-surface-2 p-4 transition-shadow hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className="flex size-9 items-center justify-center rounded-xl bg-accent/10 text-accent">
                      <Sparkles className="size-4" />
                    </span>
                    <div>
                      <Link
                        to={`/agents/${agent.id}`}
                        className="font-medium hover:text-accent"
                      >
                        {agent.name}
                      </Link>
                      <div className="text-[11px] text-muted">
                        v{agent.current_version?.version ?? "?"} ·{" "}
                        {agent.current_version?.model.split("/")[1] ??
                          agent.current_version?.model}
                        {agent.is_archived ? " · archived" : ""}
                      </div>
                    </div>
                  </div>
                </div>
                <p className="mt-2 line-clamp-2 min-h-8 text-xs text-muted">
                  {agent.description ?? agent.current_version?.system_prompt}
                </p>
                <div className="mt-3 flex gap-1.5 opacity-0 transition-opacity group-hover:opacity-100">
                  <Button
                    variant="outline"
                    className="!px-2 !py-1 text-xs"
                    onClick={() => navigate(`/agents/${agent.id}`)}
                  >
                    Edit
                  </Button>
                  <Button
                    variant="outline"
                    className="!px-2 !py-1 text-xs"
                    onClick={() => navigate(`/?agent=${agent.id}`)}
                  >
                    <MessageSquare className="size-3" /> Chat
                  </Button>
                  <Button
                    variant="ghost"
                    className="!px-2 !py-1 text-xs"
                    onClick={() =>
                      void api<Record<string, unknown>>(`/api/agents/${agent.id}/export`).then(
                        (data) => {
                          const blob = new Blob([JSON.stringify(data, null, 2)], {
                            type: "application/json",
                          });
                          const a = document.createElement("a");
                          a.href = URL.createObjectURL(blob);
                          a.download = `${agent.slug}.agent.json`;
                          a.click();
                          URL.revokeObjectURL(a.href);
                        },
                      )
                    }
                  >
                    <Download className="size-3" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            title="No agents yet"
            hint="Create your first retained expert — a researcher, a code detective, an incident commander."
          />
        )}
      </div>
    </div>
  );
}
