/** Public read-only shared thread (§18): no auth, no chrome. */
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Spinner } from "../components/ui";
import type { SharedThread } from "../lib/api/types";
import { Markdown } from "../lib/markdown/render";

export function SharePage() {
  const { token } = useParams();
  const [thread, setThread] = useState<SharedThread | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void fetch(`/api/share/${token}`)
      .then(async (r) => {
        if (!r.ok) throw new Error("This share link is invalid or has expired.");
        setThread((await r.json()) as SharedThread);
      })
      .catch((e: Error) => setError(e.message));
  }, [token]);

  if (error) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <div className="text-2xl">⚜️</div>
        <div className="text-sm text-muted">{error}</div>
      </div>
    );
  }
  if (!thread) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-4 py-10">
        <header className="mb-8 border-b border-line pb-4">
          <div className="text-xs text-muted">Shared from Retinue · read-only</div>
          <h1 className="mt-1 text-xl font-semibold">{thread.title ?? "Conversation"}</h1>
        </header>
        <div className="flex flex-col gap-6">
          {thread.messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-accent/10 px-4 py-2.5 whitespace-pre-wrap">
                  {message.parts
                    .filter((p) => p.type === "text")
                    .map((p) => p.content.text ?? "")
                    .join("\n\n")}
                </div>
              </div>
            ) : (
              <div key={message.id}>
                {message.parts
                  .filter((p) => p.type === "text")
                  .map((p) => (
                    <Markdown key={p.idx} text={p.content.text ?? ""} />
                  ))}
              </div>
            ),
          )}
        </div>
        <footer className="mt-12 border-t border-line pt-4 text-center text-xs text-muted">
          Your AI retinue · <span className="font-medium">retinue</span>
        </footer>
      </div>
    </div>
  );
}
