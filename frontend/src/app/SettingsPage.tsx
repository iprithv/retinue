import { ArrowLeft, KeyRound, Palette, Plug, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Field, Input } from "../components/ui";
import { api } from "../lib/api/client";
import type { ApiKey, Credential } from "../lib/api/types";
import { keys, useApiKeys, useCredentials } from "../lib/queries";
import { type Theme, useUi } from "../stores/ui";

const PROVIDERS = [
  "openai",
  "anthropic",
  "gemini",
  "groq",
  "mistral",
  "deepseek",
  "openrouter",
  "xai",
  "together_ai",
  "ollama",
];

function ProvidersSection() {
  const queryClient = useQueryClient();
  const { data: credentials } = useCredentials();
  const [provider, setProvider] = useState("openai");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: keys.credentials });
    void queryClient.invalidateQueries({ queryKey: keys.models });
  };

  const add = async () => {
    setError(null);
    try {
      await api<Credential>("/api/providers/credentials", {
        method: "POST",
        body: { provider, api_key: apiKey, base_url: baseUrl || undefined },
      });
      setApiKey("");
      setBaseUrl("");
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to save credential");
    }
  };

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Plug className="size-4" /> Providers
      </h2>
      <p className="text-sm text-muted">
        Keys are encrypted at rest (AES-256-GCM) and used only to call the provider you choose.
      </p>
      <div className="space-y-2">
        {(credentials ?? []).map((credential) => (
          <div
            key={credential.id}
            className="flex items-center justify-between rounded-xl border border-line bg-surface-2 px-4 py-2.5"
          >
            <div>
              <span className="font-medium">{credential.provider}</span>
              <span className="ml-2 font-mono text-xs text-muted">{credential.key_hint}</span>
              {credential.org ? (
                <span className="ml-2 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent uppercase">
                  org
                </span>
              ) : null}
              {credential.base_url ? (
                <div className="text-xs text-muted">{credential.base_url}</div>
              ) : null}
            </div>
            <button
              className="rounded-lg p-2 text-muted hover:bg-surface-3 hover:text-danger"
              onClick={() =>
                void api(`/api/providers/credentials/${credential.id}`, { method: "DELETE" }).then(
                  refresh,
                )
              }
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 rounded-xl border border-line bg-surface-2 p-4 sm:grid-cols-2">
        <Field label="Provider">
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm"
          >
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="API key">
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="sk-…"
          />
        </Field>
        <Field label="Base URL (optional, for proxies/Ollama)">
          <Input
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="http://localhost:11434"
          />
        </Field>
        <div className="flex items-end">
          <Button onClick={() => void add()} disabled={!apiKey}>
            Add provider
          </Button>
        </div>
        {error ? <div className="text-sm text-danger sm:col-span-2">{error}</div> : null}
      </div>
    </section>
  );
}

function ApiKeysSection() {
  const queryClient = useQueryClient();
  const { data: apiKeys } = useApiKeys();
  const [name, setName] = useState("");
  const [createdKey, setCreatedKey] = useState<string | null>(null);

  const create = async () => {
    const created = await api<ApiKey>("/api/keys", { method: "POST", body: { name } });
    setCreatedKey(created.key ?? null);
    setName("");
    void queryClient.invalidateQueries({ queryKey: keys.apiKeys });
  };

  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <KeyRound className="size-4" /> API keys
      </h2>
      <p className="text-sm text-muted">
        Programmatic access to your Retinue: <code className="font-mono">Authorization: Bearer rtn_…</code>
      </p>
      {createdKey ? (
        <div className="rounded-xl border border-accent/40 bg-accent/5 p-4">
          <div className="text-sm font-medium">Copy this key now — it is shown once:</div>
          <code className="mt-1 block font-mono text-sm break-all select-all">{createdKey}</code>
        </div>
      ) : null}
      <div className="space-y-2">
        {(apiKeys ?? []).map((key) => (
          <div
            key={key.id}
            className="flex items-center justify-between rounded-xl border border-line bg-surface-2 px-4 py-2.5"
          >
            <div>
              <span className="font-medium">{key.name}</span>
              <span className="ml-2 text-xs text-muted">
                {key.last_used_at
                  ? `last used ${new Date(key.last_used_at).toLocaleDateString()}`
                  : "never used"}
              </span>
            </div>
            <button
              className="rounded-lg p-2 text-muted hover:bg-surface-3 hover:text-danger"
              onClick={() =>
                void api(`/api/keys/${key.id}`, { method: "DELETE" }).then(() =>
                  queryClient.invalidateQueries({ queryKey: keys.apiKeys }),
                )
              }
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="key name, e.g. ci-bot"
          className="max-w-xs"
        />
        <Button onClick={() => void create()} disabled={!name.trim()}>
          Create key
        </Button>
      </div>
    </section>
  );
}

function AppearanceSection() {
  const theme = useUi((s) => s.theme);
  const setTheme = useUi((s) => s.setTheme);
  return (
    <section className="space-y-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Palette className="size-4" /> Appearance
      </h2>
      <div className="flex gap-2">
        {(["light", "dark", "system"] as Theme[]).map((option) => (
          <Button
            key={option}
            variant={theme === option ? "primary" : "outline"}
            onClick={() => setTheme(option)}
          >
            {option}
          </Button>
        ))}
      </div>
    </section>
  );
}

export function SettingsPage() {
  return (
    <div className="h-full overflow-y-auto bg-surface">
      <div className="mx-auto max-w-2xl space-y-10 px-4 py-8">
        <div className="flex items-center gap-3">
          <Link to="/" className="rounded-lg p-2 text-muted hover:bg-surface-3 hover:text-ink">
            <ArrowLeft className="size-4" />
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        </div>
        <ProvidersSection />
        <ApiKeysSection />
        <AppearanceSection />
      </div>
    </div>
  );
}
