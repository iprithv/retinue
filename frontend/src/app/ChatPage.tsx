import { useState } from "react";
import { useParams } from "react-router-dom";
import { EmptyState, Spinner } from "../components/ui";
import { useMessages, useModels } from "../lib/queries";
import { useAuth } from "../stores/auth";
import { useConversationStreamId } from "../stores/streaming";
import { Composer } from "../features/chat/Composer";
import { MessageList } from "../features/chat/MessageList";
import { ModelPicker } from "../features/chat/ModelPicker";
import { useChatController } from "../features/chat/controller";

export function ChatPage() {
  const { conversationId } = useParams();
  const user = useAuth((s) => s.user);
  const { data: models } = useModels();
  const { data: messageData, isLoading } = useMessages(conversationId);
  const { send, stop, regenerate, sendError } = useChatController(conversationId);
  const [modelOverride, setModelOverride] = useState<string | undefined>();

  const model =
    modelOverride ??
    (user?.settings?.["default_model"] as string | undefined) ??
    models?.[0]?.id;

  const streamingId = useConversationStreamId(conversationId);
  const isStreaming = Boolean(streamingId);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 border-b border-line bg-surface px-4 py-2.5">
        <ModelPicker value={model} onChange={setModelOverride} />
        {sendError ? <div className="truncate text-xs text-danger">{sendError}</div> : null}
      </header>

      {conversationId ? (
        isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <Spinner />
          </div>
        ) : (
          <MessageList
            conversationId={conversationId}
            messages={messageData?.messages ?? []}
            onRegenerate={(id) => regenerate(id, model)}
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
        onSend={(text) => send(text, model)}
        onStop={stop}
      />
    </div>
  );
}
