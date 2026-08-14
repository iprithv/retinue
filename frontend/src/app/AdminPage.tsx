/** Admin surface (§18): users, org usage, jobs, audit. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate } from "react-router-dom";
import { Button, Spinner } from "../components/ui";
import { api } from "../lib/api/client";
import type { AdminUser } from "../lib/api/types";
import { featureKeys, useAdminAudit, useAdminJobs, useAdminUsers } from "../lib/queries";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../stores/auth";

type Tab = "users" | "usage" | "jobs" | "audit";

interface OrgUsage {
  days: number;
  by_user: {
    user_id: string;
    email: string;
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    messages: number;
  }[];
}

function ts(ms: number | null): string {
  return ms ? new Date(ms).toLocaleString() : "—";
}

export function AdminPage() {
  const me = useAuth((s) => s.user);
  const isAdmin = me?.role === "owner" || me?.role === "admin";
  const [tab, setTab] = useState<Tab>("users");
  const queryClient = useQueryClient();

  const { data: users, isLoading } = useAdminUsers(isAdmin);
  const { data: jobs } = useAdminJobs(isAdmin && tab === "jobs");
  const { data: audit } = useAdminAudit(isAdmin && tab === "audit");
  const { data: usage } = useQuery({
    queryKey: ["admin-usage"],
    queryFn: () => api<OrgUsage>("/api/admin/usage"),
    enabled: isAdmin && tab === "usage",
  });

  const patchUser = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<AdminUser> }) =>
      api(`/api/admin/users/${id}`, { method: "PATCH", body: patch }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: featureKeys.adminUsers }),
  });
  const retryJob = useMutation({
    mutationFn: (id: string) => api(`/api/admin/jobs/${id}/retry`, { method: "POST", body: {} }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: featureKeys.adminJobs }),
  });

  if (!isAdmin) return <Navigate to="/" replace />;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-4 border-b border-line bg-surface px-6 py-3">
        <h1 className="text-sm font-semibold">Admin</h1>
        <nav className="flex gap-1">
          {(["users", "usage", "jobs", "audit"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1 text-xs font-medium capitalize ${
                tab === t ? "bg-surface-3 text-ink" : "text-muted hover:text-ink"
              }`}
            >
              {t}
            </button>
          ))}
        </nav>
      </header>
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="flex justify-center pt-12">
            <Spinner />
          </div>
        ) : tab === "users" ? (
          <table className="w-full max-w-4xl text-sm">
            <thead>
              <tr className="text-left text-[11px] tracking-wider text-muted uppercase">
                <th className="pb-2">Email</th>
                <th className="pb-2">Role</th>
                <th className="pb-2">Active</th>
                <th className="pb-2">Joined</th>
              </tr>
            </thead>
            <tbody>
              {(users ?? []).map((user) => (
                <tr key={user.id} className="border-t border-line">
                  <td className="py-2">{user.email}</td>
                  <td className="py-2">
                    {user.role === "owner" ? (
                      <span className="text-xs font-medium">owner</span>
                    ) : (
                      <select
                        className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-xs"
                        value={user.role}
                        onChange={(e) =>
                          patchUser.mutate({
                            id: user.id,
                            patch: { role: e.target.value as AdminUser["role"] },
                          })
                        }
                      >
                        <option value="admin">admin</option>
                        <option value="member">member</option>
                        <option value="viewer">viewer</option>
                      </select>
                    )}
                  </td>
                  <td className="py-2">
                    {user.role === "owner" ? (
                      "—"
                    ) : (
                      <input
                        type="checkbox"
                        checked={user.is_active}
                        onChange={(e) =>
                          patchUser.mutate({
                            id: user.id,
                            patch: { is_active: e.target.checked },
                          })
                        }
                      />
                    )}
                  </td>
                  <td className="py-2 text-xs text-muted">{ts(user.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : tab === "usage" ? (
          <table className="w-full max-w-4xl text-sm">
            <thead>
              <tr className="text-left text-[11px] tracking-wider text-muted uppercase">
                <th className="pb-2">User</th>
                <th className="pb-2 text-right">Messages</th>
                <th className="pb-2 text-right">Input tok</th>
                <th className="pb-2 text-right">Output tok</th>
                <th className="pb-2 text-right">Cost</th>
              </tr>
            </thead>
            <tbody>
              {(usage?.by_user ?? []).map((row) => (
                <tr key={row.user_id} className="border-t border-line">
                  <td className="py-2">{row.email}</td>
                  <td className="py-2 text-right">{row.messages}</td>
                  <td className="py-2 text-right">{row.input_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right">{row.output_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right">${row.cost_usd.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : tab === "jobs" ? (
          <table className="w-full max-w-5xl text-sm">
            <thead>
              <tr className="text-left text-[11px] tracking-wider text-muted uppercase">
                <th className="pb-2">Type</th>
                <th className="pb-2">Status</th>
                <th className="pb-2">Attempts</th>
                <th className="pb-2">Error</th>
                <th className="pb-2">Created</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody>
              {(jobs ?? []).map((job) => (
                <tr key={job.id} className="border-t border-line">
                  <td className="py-2 font-mono text-xs">{job.type}</td>
                  <td className="py-2">
                    <span
                      className={`text-xs ${
                        job.status === "done"
                          ? "text-success"
                          : job.status === "failed" || job.status === "dead"
                            ? "text-danger"
                            : "text-muted"
                      }`}
                    >
                      {job.status}
                    </span>
                  </td>
                  <td className="py-2 text-xs">{job.attempts}</td>
                  <td className="max-w-64 truncate py-2 text-xs text-muted" title={job.last_error ?? ""}>
                    {job.last_error ?? "—"}
                  </td>
                  <td className="py-2 text-xs text-muted">{ts(job.created_at)}</td>
                  <td className="py-2">
                    {job.status === "failed" || job.status === "dead" ? (
                      <Button
                        variant="outline"
                        className="!px-2 !py-0.5 text-xs"
                        onClick={() => retryJob.mutate(job.id)}
                      >
                        retry
                      </Button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <table className="w-full max-w-5xl text-sm">
            <thead>
              <tr className="text-left text-[11px] tracking-wider text-muted uppercase">
                <th className="pb-2">Action</th>
                <th className="pb-2">Target</th>
                <th className="pb-2">IP</th>
                <th className="pb-2">When</th>
              </tr>
            </thead>
            <tbody>
              {(audit ?? []).map((entry) => (
                <tr key={entry.id} className="border-t border-line">
                  <td className="py-2 font-mono text-xs">{entry.action}</td>
                  <td className="max-w-64 truncate py-2 text-xs text-muted">
                    {entry.target ?? "—"}
                  </td>
                  <td className="py-2 text-xs text-muted">{entry.ip ?? "—"}</td>
                  <td className="py-2 text-xs text-muted">{ts(entry.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
