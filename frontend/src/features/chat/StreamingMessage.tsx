/** The single subscriber of an in-flight message (§6.3): a token arriving
 * re-renders this component and nothing else. */
import { Markdown } from "../../lib/markdown/render";
import { useStreamingMessage } from "../../stores/streaming";

export function StreamingMessage({ messageId }: { messageId: string }) {
  const snapshot = useStreamingMessage(messageId);
  if (!snapshot) return null;

  const text = snapshot.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n\n");

  return (
    <div>
      {text ? (
        <div className={snapshot.status === "streaming" ? "stream-caret" : undefined}>
          <Markdown text={text} />
        </div>
      ) : snapshot.status === "streaming" ? (
        <div className="stream-caret text-muted" />
      ) : null}
      {snapshot.error ? (
        <div className="mt-2 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {snapshot.error.message}
        </div>
      ) : null}
    </div>
  );
}
