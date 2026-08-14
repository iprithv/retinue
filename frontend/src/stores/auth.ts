/** Auth session: access token lives in memory only; the refresh token lives in
 * an httpOnly cookie the JS never sees. boot() silently restores a session via
 * the cookie + CSRF double-submit header (§16). */
import { create } from "zustand";
import type { TokenResponse, User } from "../lib/api/types";

export function readCsrfCookie(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)retinue_csrf=([^;]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

interface AuthState {
  status: "booting" | "authed" | "anon";
  user: User | null;
  accessToken: string | null;
  setSession: (session: TokenResponse) => void;
  setUser: (user: User) => void;
  boot: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  logout: () => Promise<void>;
}

async function authPost(path: string, body?: unknown): Promise<TokenResponse> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const csrf = readCsrfCookie();
  if (csrf) headers["x-csrf-token"] = csrf;
  const response = await fetch(path, {
    method: "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  if (!response.ok) {
    const envelope = await response.json().catch(() => null);
    throw new Error(envelope?.error?.message ?? `request failed (${response.status})`);
  }
  return response.json() as Promise<TokenResponse>;
}

export const useAuth = create<AuthState>((set, get) => ({
  status: "booting",
  user: null,
  accessToken: null,

  setSession: (session) =>
    set({ status: "authed", user: session.user, accessToken: session.access_token }),

  setUser: (user) => set({ user }),

  boot: async () => {
    try {
      const session = await authPost("/api/auth/refresh");
      get().setSession(session);
    } catch {
      set({ status: "anon", user: null, accessToken: null });
    }
  },

  login: async (email, password) => {
    get().setSession(await authPost("/api/auth/login", { email, password }));
  },

  register: async (email, password, name) => {
    get().setSession(await authPost("/api/auth/register", { email, password, name }));
  },

  logout: async () => {
    try {
      await authPost("/api/auth/logout");
    } catch {
      // local teardown regardless
    }
    set({ status: "anon", user: null, accessToken: null });
  },
}));

/** Refresh the access token via the cookie; returns the new token or null. */
export async function refreshAccessToken(): Promise<string | null> {
  try {
    const session = await authPost("/api/auth/refresh");
    useAuth.getState().setSession(session);
    return session.access_token;
  } catch {
    useAuth.setState({ status: "anon", user: null, accessToken: null });
    return null;
  }
}
