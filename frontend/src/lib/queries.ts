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
    queryFn: () => api<MessageList>(`/api/conversations/${conversationId}/messages`),
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
