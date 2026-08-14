/** Data sources (§30): connect any of 25+ database engines as read-only agent
 * sources, with the staged connection-test ladder and a guarded query console. */
import { useQueryClient } from "@tanstack/react-query";
import { Check, Database, Play, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Button, Input } from "../../components/ui";
import { api } from "../../lib/api/client";
import type { DataSource, QueryResultData, TestStage } from "../../lib/api/types";
import { dsKeys, useDataSources, useEngines } from "../../lib/queries";

function StageLadder({ stages }: { stages: TestStage[] }) {
  return (
    <div className="mt-1 flex flex-wrap gap-2 text-[11px]">
      {stages.map((stage) => (
        <span
          key={stage.stage}
          className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${
            stage.ok ? "border-success/40 text-success" : "border-danger/40 text-danger"
          }`}
          title={stage.detail}
        >
          {stage.ok ? <Check className="size-3" /> : <X className="size-3" />}
          {stage.stage} · {stage.latency_ms}ms
        </span>
      ))}
    </div>
  );
}

function QueryConsole({ source }: { source: DataSource }) {
  const [statement, setStatement] = useState("");
  const [result, setResult] = useState<QueryResultData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async () => {
    if (!statement.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setResult(
        await api<QueryResultData>(`/api/datasources/${source.id}/query`, {
          method: "POST",
          body: { statement },
        }),
      );
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "query failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-2 border-t border-line pt-2">
      <div className="flex gap-1.5">
        <input
          className="flex-1 rounded-lg border border-line bg-surface px-2.5 py-1.5 font-mono text-xs outline-none focus:border-accent/60"
          placeholder="SELECT … (read-only, auto-limited)"
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
        />
        <Button variant="outline" className="!px-2" onClick={() => void run()} disabled={busy}>
          <Play className="size-3.5" />
        </Button>
      </div>
      {error ? <div className="mt-1 text-xs text-danger">{error}</div> : null}
      {result ? (
        <div className="mt-2 max-h-56 overflow-auto rounded-lg border border-line">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-3">
              <tr>
                {result.columns.map((column) => (
                  <th key={column} className="px-2 py-1 text-left font-semibold">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {result.rows.map((row, i) => (
                <tr key={i} className="border-t border-line">
                  {row.map((cell, j) => (
                    <td key={j} className="max-w-56 truncate px-2 py-1">
                      {cell === null ? "∅" : String(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="border-t border-line bg-surface-2 px-2 py-1 text-[10px] text-muted">
            {result.row_count} rows{result.truncated ? " (truncated)" : ""} ·{" "}
            {result.elapsed_ms}ms
          </div>
        </div>
      ) : null}
    </div>
  );
}

function SourceCard({ source }: { source: DataSource }) {
  const queryClient = useQueryClient();
  const [testing, setTesting] = useState(false);
  const [console_, setConsole] = useState(false);
  const refresh = () => void queryClient.invalidateQueries({ queryKey: dsKeys.sources });

  const test = async () => {
    setTesting(true);
    try {
      await api(`/api/datasources/${source.id}/test`, { method: "POST", body: {} });
    } finally {
      setTesting(false);
      refresh();
    }
  };

  return (
    <li className="rounded-xl border border-line bg-surface-2 px-3 py-2 text-sm">
      <div className="flex items-center gap-2">
        <Database className="size-3.5 text-accent" />
        <span className="font-medium">{source.name}</span>
        <span className="text-xs text-muted">{source.engine_label}</span>
        <span
          className={`text-[10px] uppercase ${
            source.status === "ok"
              ? "text-success"
              : source.status === "failed"
                ? "text-danger"
                : "text-muted"
          }`}
        >
          {source.status}
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <Button
            variant="outline"
            className="!px-2 !py-0.5 text-xs"
            onClick={() => void test()}
            disabled={testing}
          >
            {testing ? "testing…" : "test"}
          </Button>
          <Button
            variant="ghost"
            className="!px-2 !py-0.5 text-xs"
            onClick={() => setConsole(!console_)}
          >
            query
          </Button>
          <button
            className="rounded p-1 text-muted hover:text-danger"
            onClick={() =>
              void api(`/api/datasources/${source.id}`, { method: "DELETE" }).then(refresh)
            }
          >
            <Trash2 className="size-3.5" />
          </button>
        </span>
      </div>
      {source.last_test ? <StageLadder stages={source.last_test.stages} /> : null}
      {console_ ? <QueryConsole source={source} /> : null}
    </li>
  );
}

export function DataSourcesSection() {
  const queryClient = useQueryClient();
  const { data: engines } = useEngines();
  const { data: sources } = useDataSources();
  const [engineKey, setEngineKey] = useState("postgres");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const engine = engines?.find((e) => e.key === engineKey);

  const add = async () => {
    setError(null);
    try {
      await api("/api/datasources", {
        method: "POST",
        body: { name: name || engine?.label || engineKey, engine: engineKey, config, secrets },
      });
      setOpen(false);
      setName("");
      setConfig({});
      setSecrets({});
      void queryClient.invalidateQueries({ queryKey: dsKeys.sources });
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to add source");
    }
  };

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Database className="size-4" /> Data sources
      </h2>
      <p className="text-sm text-muted">
        Connect databases your agents can query — read-only by construction: single-SELECT
        AST validation, automatic row limits, table allow/deny lists, PII masking, and a
        full audit trail. {engines?.length ?? "25+"} engines supported.
      </p>

      {!open ? (
        <Button variant="outline" onClick={() => setOpen(true)}>
          <Plus className="size-3.5" /> Connect a database
        </Button>
      ) : (
        <div className="space-y-2 rounded-xl border border-line bg-surface-2 p-3">
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs font-semibold text-muted">
              Engine
              <select
                className="mt-1 w-full rounded-lg border border-line bg-surface px-2 py-1.5 text-sm"
                value={engineKey}
                onChange={(e) => {
                  setEngineKey(e.target.value);
                  setConfig({});
                  setSecrets({});
                }}
              >
                {(engines ?? []).map((e) => (
                  <option key={e.key} value={e.key}>
                    {e.label}
                    {e.available ? "" : ` (needs retinue[${e.install_extra}])`}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs font-semibold text-muted">
              Name
              <Input
                className="mt-1"
                placeholder={engine?.label}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {(engine?.config_fields ?? []).map((field) => (
              <label key={field.name} className="text-xs font-semibold text-muted">
                {field.name}
                {field.required ? " *" : ""}
                <Input
                  className="mt-1"
                  placeholder={field.hint || String(field.default ?? "")}
                  value={config[field.name] ?? ""}
                  onChange={(e) => setConfig({ ...config, [field.name]: e.target.value })}
                />
              </label>
            ))}
            {(engine?.secret_fields ?? []).map((secret) => (
              <label key={secret} className="text-xs font-semibold text-muted">
                {secret} 🔒
                <Input
                  className="mt-1"
                  type="password"
                  value={secrets[secret] ?? ""}
                  onChange={(e) => setSecrets({ ...secrets, [secret]: e.target.value })}
                />
              </label>
            ))}
          </div>
          {engine?.notes ? <div className="text-[11px] text-muted">{engine.notes}</div> : null}
          {engine?.file_based ? (
            <div className="text-[11px] text-warn">
              File-based engines read server-local paths — admin role required.
            </div>
          ) : null}
          {error ? <div className="text-xs text-danger">{error}</div> : null}
          <div className="flex gap-2">
            <Button onClick={() => void add()}>Connect</Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <ul className="space-y-1.5">
        {(sources ?? []).map((source) => (
          <SourceCard key={source.id} source={source} />
        ))}
      </ul>
    </section>
  );
}
