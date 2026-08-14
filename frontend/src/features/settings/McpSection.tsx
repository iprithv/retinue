/** MCP servers manager (§9.3): add/test/toggle, tool list preview. */
import { useQueryClient } from "@tanstack/react-query";
import { Plug2, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api/client";
import type { McpServer } from "../../lib/api/types";
import { featureKeys, useMcpServers } from "../../lib/queries";

function ServerRow({ server }: { server: McpServer }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<string | null>(null);
  const [tools, setTools] = useState<string[] | null>(null);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: featureKeys.mcpServers });

  const test = async () => {
    setStatus("testing…");
    const result = await api<{ ok: boolean; tools?: number; error?: string }>(
      `/api/mcp/servers/${server.id}/test`,
      { method: "POST", body: {} },
    );
    setStatus(result.ok ? `ok · ${result.tools} tools` : `failed: ${result.error}`);
    if (result.ok) {
      const list = await api<{ name: string }[]>(`/api/mcp/servers/${server.id}/tools`);
      setTools(list.map((t) => t.name));
    }
  };

  return (
    <li className="rounded-xl border border-line bg-surface-2 px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="font-medium">{server.name}</span>
        <span className="text-xs text-muted">{server.transport}</span>
        {server.org ? <span className="text-[10px] text-accent">org</span> : null}
        <span className="ml-auto flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-muted">
            <input
              type="checkbox"
              checked={server.enabled}
              onChange={(e) =>
                void api(`/api/mcp/servers/${server.id}`, {
                  method: "PATCH",
                  body: { enabled: e.target.checked },
                }).then(refresh)
              }
            />
            enabled
          </label>
          <Button variant="outline" className="!px-2 !py-0.5 text-xs" onClick={() => void test()}>
            test
          </Button>
          <button
            className="rounded p-1 text-muted hover:text-danger"
            onClick={() =>
              void api(`/api/mcp/servers/${server.id}`, { method: "DELETE" }).then(refresh)
            }
          >
            <Trash2 className="size-3.5" />
          </button>
        </span>
      </div>
      {status ? <div className="mt-1 text-xs text-muted">{status}</div> : null}
      {tools ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {tools.map((t) => (
            <span key={t} className="rounded-full border border-line px-2 py-0.5 font-mono text-[10px]">
              {t}
            </span>
          ))}
        </div>
      ) : null}
    </li>
  );
}

export function McpSection() {
  const queryClient = useQueryClient();
  const { data: servers } = useMcpServers();
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"stdio" | "http">("stdio");
  const [command, setCommand] = useState("");
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  const add = async () => {
    setError(null);
    try {
      const [cmd, ...args] = command.trim().split(/\s+/);
      await api("/api/mcp/servers", {
        method: "POST",
        body:
          transport === "stdio"
            ? { name, transport, command: cmd, args }
            : { name, transport, url },
      });
      setName("");
      setCommand("");
      setUrl("");
      void queryClient.invalidateQueries({ queryKey: featureKeys.mcpServers });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add server");
    }
  };

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Plug2 className="size-4" /> MCP servers
      </h2>
      <p className="text-sm text-muted">
        Attach Model Context Protocol servers to your agents. Results are always treated as
        untrusted data.
      </p>
      <div className="grid grid-cols-[1fr_auto] gap-1.5 sm:grid-cols-[10rem_auto_1fr_auto]">
        <Input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
        <select
          className="rounded-lg border border-line bg-surface-2 px-2 text-sm"
          value={transport}
          onChange={(e) => setTransport(e.target.value as "stdio" | "http")}
        >
          <option value="stdio">stdio</option>
          <option value="http">http</option>
        </select>
        {transport === "stdio" ? (
          <Input
            placeholder="command and args, e.g. npx -y @modelcontextprotocol/server-filesystem /tmp"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
          />
        ) : (
          <Input placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
        )}
        <Button
          onClick={() => void add()}
          disabled={!name.trim() || !(transport === "stdio" ? command.trim() : url.trim())}
        >
          <Plus className="size-3.5" />
        </Button>
      </div>
      {error ? <div className="text-xs text-danger">{error}</div> : null}
      <ul className="space-y-1.5">
        {(servers ?? []).map((server) => (
          <ServerRow key={server.id} server={server} />
        ))}
      </ul>
    </section>
  );
}
