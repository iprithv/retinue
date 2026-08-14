/** Connector gallery (§28.6): one-click integrations riding the MCP/actions bus. */
import { useQueryClient } from "@tanstack/react-query";
import { Blocks, Check } from "lucide-react";
import { useState } from "react";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api/client";
import type { ConnectorEntry } from "../../lib/api/types";
import { featureKeys, useConnectors } from "../../lib/queries";

const CATEGORY_LABELS: Record<string, string> = {
  chat: "Chat & collaboration",
  dev: "Developer tools",
  tickets: "Tickets & projects",
  observability: "Observability",
  incidents: "Incident management",
  cloud: "Cloud & infrastructure",
  docs: "Docs & drive",
  crm: "Support & CRM",
};

function InstallDialog({
  connector,
  onClose,
}: {
  connector: ConnectorEntry;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [params, setParams] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const install = async () => {
    setError(null);
    try {
      const result = await api<{ kind: string; note: string | null }>(
        `/api/connectors/${connector.key}/install`,
        { method: "POST", body: { secrets, params } },
      );
      setDone(
        result.kind === "mcp"
          ? `Installed as an MCP server${result.note ? ` — ${result.note}` : ""}. Attach it to agents in the studio.`
          : "Installed as an API action. Attach it to agents in the studio.",
      );
      void queryClient.invalidateQueries({ queryKey: featureKeys.mcpServers });
      void queryClient.invalidateQueries({ queryKey: featureKeys.actions });
    } catch (e) {
      setError(e instanceof Error ? e.message : "install failed");
    }
  };

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-line bg-surface p-3">
      {done ? (
        <div className="flex items-center gap-2 text-sm text-success">
          <Check className="size-4" /> {done}
        </div>
      ) : (
        <>
          {connector.params.map((param) => (
            <label key={param.name} className="block text-xs font-semibold text-muted">
              {param.label}
              {param.required ? " *" : ""}
              <Input
                className="mt-1"
                value={params[param.name] ?? param.default}
                onChange={(e) => setParams({ ...params, [param.name]: e.target.value })}
              />
            </label>
          ))}
          {connector.secrets.map((secret) => (
            <label key={secret.name} className="block text-xs font-semibold text-muted">
              {secret.label}
              {secret.required ? " *" : ""} 🔒
              <Input
                className="mt-1"
                type="password"
                value={secrets[secret.name] ?? ""}
                onChange={(e) => setSecrets({ ...secrets, [secret.name]: e.target.value })}
              />
            </label>
          ))}
          {connector.runtime ? (
            <div className="text-[11px] text-muted">
              Requires <code>{connector.runtime}</code> on the server host.
            </div>
          ) : null}
          {connector.docs ? (
            <div className="text-[11px] text-muted">{connector.docs}</div>
          ) : null}
          {error ? <div className="text-xs text-danger">{error}</div> : null}
        </>
      )}
      <div className="flex gap-2">
        {!done ? <Button onClick={() => void install()}>Install</Button> : null}
        <Button variant="ghost" onClick={onClose}>
          {done ? "Close" : "Cancel"}
        </Button>
      </div>
    </div>
  );
}

export function ConnectorsSection() {
  const { data: connectors } = useConnectors();
  const [open, setOpen] = useState<string | null>(null);

  const grouped = new Map<string, ConnectorEntry[]>();
  for (const connector of connectors ?? []) {
    const bucket = grouped.get(connector.category) ?? [];
    bucket.push(connector);
    grouped.set(connector.category, bucket);
  }

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Blocks className="size-4" /> Connectors
      </h2>
      <p className="text-sm text-muted">
        One-click integrations — each installs a pinned MCP server or a minimal API action
        with encrypted credentials. Results are always treated as untrusted data.
      </p>
      {[...grouped.entries()].map(([category, entries]) => (
        <div key={category}>
          <h3 className="mb-1.5 text-xs font-semibold tracking-wider text-muted uppercase">
            {CATEGORY_LABELS[category] ?? category}
          </h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {entries.map((connector) => (
              <div
                key={connector.key}
                className="rounded-xl border border-line bg-surface-2 px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-medium">{connector.name}</span>
                  <Button
                    variant="outline"
                    className="!px-2 !py-0.5 text-xs"
                    onClick={() => setOpen(open === connector.key ? null : connector.key)}
                  >
                    {open === connector.key ? "close" : "add"}
                  </Button>
                </div>
                <p className="mt-0.5 text-xs text-muted">{connector.description}</p>
                {open === connector.key ? (
                  <InstallDialog connector={connector} onClose={() => setOpen(null)} />
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}
