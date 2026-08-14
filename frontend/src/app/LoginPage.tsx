import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Field, Input } from "../components/ui";
import { useAuth } from "../stores/auth";

export function LoginPage() {
  const navigate = useNavigate();
  const login = useAuth((s) => s.login);
  const register = useAuth((s) => s.register);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, password, name || undefined);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "something went wrong");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center bg-surface p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="text-4xl">⚜️</div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight">retinue</h1>
          <p className="mt-1 text-sm text-muted">Your AI retinue. Every expert, in attendance.</p>
        </div>
        <form onSubmit={submit} className="space-y-4 rounded-2xl border border-line bg-surface-2 p-6 shadow-sm">
          {mode === "register" ? (
            <Field label="Name">
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="optional" />
            </Field>
          ) : null}
          <Field label="Email">
            <Input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={mode === "register" ? "at least 8 characters" : "••••••••"}
            />
          </Field>
          {error ? <div className="text-sm text-danger">{error}</div> : null}
          <Button type="submit" disabled={busy} className="w-full py-2">
            {busy ? "…" : mode === "login" ? "Sign in" : "Create account"}
          </Button>
          <button
            type="button"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
            className="w-full text-center text-xs text-muted hover:text-ink"
          >
            {mode === "login" ? "First time here? Create an account" : "Already set up? Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
