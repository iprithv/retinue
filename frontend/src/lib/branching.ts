/** Branch-tree semantics (§17): the visible thread is the path from root
 * following, at each fork, the client-selected child (newest by default).
 * Siblings are alternatives created by edit or regenerate. */
import type { Message } from "./api/types";

const ROOT = "__root__";

export interface SiblingInfo {
  parentKey: string;
  index: number; // 1-based position among siblings
  count: number;
  siblingIds: string[];
}

export interface ThreadView {
  thread: Message[];
  siblings: Map<string, SiblingInfo>;
}

export function computeThread(
  messages: Message[],
  selections: Record<string, string> | undefined,
): ThreadView {
  const children = new Map<string, Message[]>();
  for (const message of messages) {
    const key = message.parent_id ?? ROOT;
    const bucket = children.get(key) ?? [];
    bucket.push(message);
    children.set(key, bucket);
  }
  for (const bucket of children.values()) {
    bucket.sort((a, b) => a.created_at - b.created_at || a.id.localeCompare(b.id));
  }

  const thread: Message[] = [];
  const siblings = new Map<string, SiblingInfo>();
  let key = ROOT;
  for (;;) {
    const bucket = children.get(key);
    if (!bucket || bucket.length === 0) break;
    const selectedId = selections?.[key];
    const chosen: Message =
      (selectedId ? bucket.find((m) => m.id === selectedId) : undefined) ??
      bucket[bucket.length - 1]!;
    if (bucket.length > 1) {
      siblings.set(chosen.id, {
        parentKey: key,
        index: bucket.indexOf(chosen) + 1,
        count: bucket.length,
        siblingIds: bucket.map((m) => m.id),
      });
    }
    thread.push(chosen);
    key = chosen.id;
  }
  return { thread, siblings };
}

export { ROOT as ROOT_KEY };
