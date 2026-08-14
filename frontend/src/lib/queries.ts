/** TanStack Query owns everything persisted (§6.3 rule 1). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api/client";
import type {
  ApiKey,
  Conversation,
  Credential,
  MessageList,
  ModelInfo,
  UsageSummary,
  User,
} from "./api/types";

export const keys = {
  conversations: ["conversations"] as const,
  messages: (conversationId: string) => ["messages", conversationId] as const,
  models: ["models"] as const,
  credentials: ["credentials"] as const,
  apiKeys: ["api-keys"] as const,
  usage: (days: number) => ["usage", days] as const,
};

export function useConversations() {
  return useQuery({
    queryKey: keys.conversations,
    queryFn: () => api<Conversation[]>("/api/conversations"),
    staleTime: 15_000,
  });
}

export function useMessages(conversationId: string | undefined) {
  return useQuery({
    queryKey: keys.messages(conversationId ?? "none"),
    queryFn: () => api<MessageList>(`/api/conversations/${conversationId}/messages?all=true`),
    enabled: Boolean(conversationId),
  });
}

export function useModels() {
  return useQuery({
    queryKey: keys.models,
    queryFn: () => api<ModelInfo[]>("/api/models"),
    staleTime: 5 * 60_000,
  });
}

export function useCredentials() {
  return useQuery({
    queryKey: keys.credentials,
    queryFn: () => api<Credential[]>("/api/providers/credentials"),
  });
}

export function useApiKeys() {
  return useQuery({ queryKey: keys.apiKeys, queryFn: () => api<ApiKey[]>("/api/keys") });
}

export function useUsage(days: number) {
  return useQuery({
    queryKey: keys.usage(days),
    queryFn: () => api<UsageSummary>(`/api/usage/summary?days=${days}`),
  });
}

export function usePatchConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<Conversation> }) =>
      api<Conversation>(`/api/conversations/${id}`, { method: "PATCH", body: patch }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.conversations }),
  });
}

export function useDeleteConversation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/conversations/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.conversations }),
  });
}

export function usePatchMe() {
  return useMutation({
    mutationFn: (patch: { name?: string; settings?: Record<string, unknown> }) =>
      api<User>("/api/auth/me", { method: "PATCH", body: patch }),
  });
}

// -- v0.2+ feature hooks ---------------------------------------------------------

import type {
  ActionInfo,
  AdminUser,
  Agent,
  AgentBehaviorPayload,
  AuditEntry,
  Collection,
  CollectionStatus,
  FileInfo,
  JobInfo,
  McpServer,
  Memory,
  SearchHit,
} from "./api/types";

export const featureKeys = {
  agents: ["agents"] as const,
  agent: (id: string) => ["agent", id] as const,
  agentVersions: (id: string) => ["agent-versions", id] as const,
  files: ["files"] as const,
  collections: ["collections"] as const,
  collectionStatus: (id: string) => ["collection-status", id] as const,
  memories: ["memories"] as const,
  mcpServers: ["mcp-servers"] as const,
  actions: ["actions"] as const,
  adminUsers: ["admin-users"] as const,
  adminJobs: ["admin-jobs"] as const,
  adminAudit: ["admin-audit"] as const,
};

export function useAgents() {
  return useQuery({
    queryKey: featureKeys.agents,
    queryFn: () => api<Agent[]>("/api/agents"),
    staleTime: 15_000,
  });
}

export function useAgent(id: string | undefined) {
  return useQuery({
    queryKey: featureKeys.agent(id ?? "none"),
    queryFn: () => api<Agent>(`/api/agents/${id}`),
    enabled: Boolean(id),
  });
}

export function useAgentVersions(id: string | undefined) {
  return useQuery({
    queryKey: featureKeys.agentVersions(id ?? "none"),
    queryFn: () => api<import("./api/types").AgentVersion[]>(`/api/agents/${id}/versions`),
    enabled: Boolean(id),
  });
}

export function useSaveAgentVersion(agentId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (behavior: AgentBehaviorPayload) =>
      api(`/api/agents/${agentId}/versions`, { method: "POST", body: behavior }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: featureKeys.agent(agentId) });
      void queryClient.invalidateQueries({ queryKey: featureKeys.agentVersions(agentId) });
      void queryClient.invalidateQueries({ queryKey: featureKeys.agents });
    },
  });
}

export function useFiles() {
  return useQuery({ queryKey: featureKeys.files, queryFn: () => api<FileInfo[]>("/api/files") });
}

export function useCollections() {
  return useQuery({
    queryKey: featureKeys.collections,
    queryFn: () => api<Collection[]>("/api/collections"),
  });
}

export function useCollectionStatus(id: string | undefined, poll = false) {
  return useQuery({
    queryKey: featureKeys.collectionStatus(id ?? "none"),
    queryFn: () => api<CollectionStatus>(`/api/collections/${id}/status`),
    enabled: Boolean(id),
    refetchInterval: poll ? 1500 : false,
  });
}

export function useMemories() {
  return useQuery({
    queryKey: featureKeys.memories,
    queryFn: () => api<Memory[]>("/api/memories"),
  });
}

export function useMcpServers() {
  return useQuery({
    queryKey: featureKeys.mcpServers,
    queryFn: () => api<McpServer[]>("/api/mcp/servers"),
  });
}

export function useActions() {
  return useQuery({ queryKey: featureKeys.actions, queryFn: () => api<ActionInfo[]>("/api/actions") });
}

export function useAdminUsers(enabled: boolean) {
  return useQuery({
    queryKey: featureKeys.adminUsers,
    queryFn: () => api<AdminUser[]>("/api/admin/users"),
    enabled,
  });
}

export function useAdminJobs(enabled: boolean) {
  return useQuery({
    queryKey: featureKeys.adminJobs,
    queryFn: () => api<JobInfo[]>("/api/admin/jobs"),
    enabled,
    refetchInterval: 5000,
  });
}

export function useAdminAudit(enabled: boolean) {
  return useQuery({
    queryKey: featureKeys.adminAudit,
    queryFn: () => api<AuditEntry[]>("/api/admin/audit"),
    enabled,
  });
}

export async function searchAll(q: string, scope = "all"): Promise<SearchHit[]> {
  const result = await api<{ query: string; hits: SearchHit[] }>(
    `/api/search?q=${encodeURIComponent(q)}&scope=${scope}`,
  );
  return result.hits;
}

// -- data sources & connectors -----------------------------------------------------

import type { ConnectorEntry, DataSource, EngineCatalogEntry } from "./api/types";

export const dsKeys = {
  engines: ["ds-engines"] as const,
  sources: ["ds-sources"] as const,
  connectors: ["connectors"] as const,
};

export function useEngines() {
  return useQuery({
    queryKey: dsKeys.engines,
    queryFn: () => api<EngineCatalogEntry[]>("/api/datasources/engines"),
    staleTime: 5 * 60_000,
  });
}

export function useDataSources() {
  return useQuery({
    queryKey: dsKeys.sources,
    queryFn: () => api<DataSource[]>("/api/datasources"),
  });
}

export function useConnectors() {
  return useQuery({
    queryKey: dsKeys.connectors,
    queryFn: () => api<ConnectorEntry[]>("/api/connectors"),
    staleTime: 5 * 60_000,
  });
}
