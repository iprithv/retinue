import { useState } from "react";
import { useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { EmptyState, Spinner } from "../components/ui";
import { api } from "../lib/api/client";
import type { Conversation } from "../lib/api/types";
import { keys, useAgent, useConversations, useMessages, useModels } from "../lib/queries";
import { useAuth } from "../stores/auth";
import { useConversationStreamId } from "../stores/streaming";
import { AgentPicker } from "../features/chat/AgentPicker";
import { Composer } from "../features/chat/Composer";
import { MessageList } from "../features/chat/MessageList";
import { ModelPicker } from "../features/chat/ModelPicker";
import { useChatController } from "../features/chat/controller";

function RepinBanner({ conversation }: { conversation: Conversation }) {
  const { data: agent } = useAgent(conversation.agent_id ?? undefined);
  const queryClient = useQueryClient();
  if (
    !agent?.current_version ||
    !conversation.agent_version_id ||
    agent.current_version.id === conversation.agent_version_id
  ) {
    return null;
  }
  return (
    <div className="flex items-center justify-center gap-2 border-b border-line bg-accent/5 px-4 py-1.5 text-xs">
      <span>
        This chat is pinned to an older version of <b>{agent.name}</b> (now v
        {agent.current_version.version}).
      </span>
      <button
        className="font-medium text-accent underline-offset-2 hover:underline"
        onClick={() =>
          void api(`/api/conversations/${conversation.id}/repin-agent`, {
            method: "POST",
            body: {},
          }).then(() => queryClient.invalidateQueries({ queryKey: keys.conversations }))
        }
      >
        Update this chat to v{agent.current_version.version}
      </button>
    </div>
  );
}

export function ChatPage() {
  const { conversationId } = useParams();
  const user = useAuth((s) => s.user);
  const { data: models } = useModels();
  const { data: conversations } = useConversations();
  const { data: messageData, isLoading } = useMessages(conversationId);
  const { send, stop, regenerate, resend, approve, sendError } =
    useChatController(conversationId);
  const [modelOverride, setModelOverride] = useState<string | undefined>();
  const [agentId, setAgentId] = useState<string | undefined>();

  const conversation = conversations?.find((c) => c.id === conversationId);

  const model =
    modelOverride ??
    (user?.settings?.["default_model"] as string | undefined) ??
    models?.[0]?.id;

  const streamingId = useConversationStreamId(conversationId);
  const isStreaming = Boolean(streamingId);

  // agent-pinned chats use the agent's model unless the user explicitly
  // overrides it in the picker (§9.1 precedence)
  const agentPinned = Boolean(agentId || conversation?.agent_id);
  const effectiveModel = agentPinned ? modelOverride : model;

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5">
        <div className="flex items-center gap-2">
          <ModelPicker value={model} onChange={setModelOverride} />
          {!conversationId ? <AgentPicker value={agentId} onChange={setAgentId} /> : null}
        </div>
        {sendError ? <div className="truncate text-xs text-danger">{sendError}</div> : null}
      </header>
      {conversation ? <RepinBanner conversation={conversation} /> : null}

      {conversationId ? (
        isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <MessageList
            conversationId={conversationId}
            messages={messageData?.messages ?? []}
            onRegenerate={(id) => regenerate(id, effectiveModel)}
            onResend={resend}
            onApprove={approve}
          />
        )
      ) : (
        <EmptyState
          title="Assemble your retinue"
          hint={
            (models?.length ?? 0) > 0
              ? "Every expert you need, in attendance. Ask anything below."
              : "Add a provider API key in Settings (or export OPENAI_API_KEY / ANTHROPIC_API_KEY before starting the server), then ask anything below."
          }
        />
      )}

      <Composer
        conversationId={conversationId}
        streaming={isStreaming}
        onSend={(text, fileIds) => send(text, effectiveModel, { agentId, fileIds })}
        onStop={stop}
      />
    </div>
  );
}
