/** The anti-jank contract (§6.3): in-flight assistant messages live here, not
 * in React state and not in the query cache. Token deltas append to mutable
 * buffers; flushes coalesce to at most one per animation frame; and only the
 * `<StreamingMessage/>` subscribed to that message id re-renders. */
import { useSyncExternalStore } from "react";

export interface StreamingPart {
  index: number;
  type: string;
  text: string;
}

export interface StreamError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface StreamingSnapshot {
  messageId: string;
  conversationId: string;
  model: string | null;
  parts: readonly StreamingPart[];
  status: "streaming" | "done" | "error";
  stopReason: string | null;
  error: StreamError | null;
  version: number;
}

type Listener = () => void;

const raf: (cb: () => void) => number =
  typeof requestAnimationFrame === "function"
    ? (cb) => requestAnimationFrame(cb)
    : (cb) => setTimeout(cb, 16) as unknown as number;

class StreamingStore {
  private snapshots = new Map<string, StreamingSnapshot>();
  private buffers = new Map<string, Map<number, { type: string; text: string }>>();
  private listeners = new Map<string, Set<Listener>>();
  private anyListeners = new Set<Listener>();
  private byConversation = new Map<string, string>();
  private dirty = new Set<string>();
  private rafPending = false;
  private globalVersion = 0;

  // -- producer side (chat controller) --------------------------------------

  start(messageId: string, conversationId: string, model: string | null): void {
    this.buffers.set(messageId, new Map());
    this.snapshots.set(messageId, {
      messageId,
      conversationId,
      model,
      parts: [],
      status: "streaming",
      stopReason: null,
      error: null,
      version: 0,
    });
    this.byConversation.set(conversationId, messageId);
    this.notifyNow(messageId);
  }

  blockStart(messageId: string, index: number, type: string): void {
    const buffer = this.buffers.get(messageId);
    if (!buffer) return;
    if (!buffer.has(index)) buffer.set(index, { type, text: "" });
    this.markDirty(messageId);
  }

  appendDelta(messageId: string, index: number, text: string): void {
    const buffer = this.buffers.get(messageId);
    if (!buffer) return;
    let block = buffer.get(index);
    if (!block) {
      block = { type: "text", text: "" };
      buffer.set(index, block);
    }
    block.text += text;
    this.markDirty(messageId);
  }

  /** A resumed stream replays from scratch: reset buffers, keep identity. */
  resetParts(messageId: string): void {
    this.buffers.get(messageId)?.clear();
    this.markDirty(messageId);
  }

  fail(messageId: string, error: StreamError): void {
    const snapshot = this.snapshots.get(messageId);
    if (!snapshot) return;
    this.snapshots.set(messageId, { ...this.rebuild(messageId), status: "error", error });
    this.notifyNow(messageId);
  }

  finish(messageId: string, stopReason: string): void {
    const snapshot = this.snapshots.get(messageId);
    if (!snapshot) return;
    this.snapshots.set(messageId, {
      ...this.rebuild(messageId),
      status: snapshot.error ? "error" : "done",
      stopReason,
    });
    this.notifyNow(messageId);
  }

  clear(messageId: string): void {
    const snapshot = this.snapshots.get(messageId);
    this.snapshots.delete(messageId);
    this.buffers.delete(messageId);
    if (snapshot && this.byConversation.get(snapshot.conversationId) === messageId) {
      this.byConversation.delete(snapshot.conversationId);
    }
    this.notifyNow(messageId);
  }

  // -- flush machinery ----------------------------------------------------------

  private markDirty(messageId: string): void {
    this.dirty.add(messageId);
    if (this.rafPending) return;
    this.rafPending = true;
    raf(() => this.flush());
  }

  private rebuild(messageId: string): StreamingSnapshot {
    const previous = this.snapshots.get(messageId)!;
    const buffer = this.buffers.get(messageId) ?? new Map();
    const parts: StreamingPart[] = [...buffer.entries()]
      .sort(([a], [b]) => a - b)
      .map(([index, block]) => ({ index, type: block.type, text: block.text }));
    return { ...previous, parts, version: previous.version + 1 };
  }

  private flush(): void {
    this.rafPending = false;
    const dirty = [...this.dirty];
    this.dirty.clear();
    for (const messageId of dirty) {
      if (!this.snapshots.has(messageId)) continue;
      this.snapshots.set(messageId, this.rebuild(messageId));
      this.emit(messageId);
    }
  }

  private notifyNow(messageId: string): void {
    this.dirty.delete(messageId);
    this.emit(messageId);
  }

  private emit(messageId: string): void {
    this.globalVersion += 1;
    for (const listener of this.listeners.get(messageId) ?? []) listener();
    for (const listener of this.anyListeners) listener();
  }

  // -- subscriber side ------------------------------------------------------------

  subscribe(messageId: string, listener: Listener): () => void {
    let set = this.listeners.get(messageId);
    if (!set) {
      set = new Set();
      this.listeners.set(messageId, set);
    }
    set.add(listener);
    return () => {
      set.delete(listener);
      if (set.size === 0) this.listeners.delete(messageId);
    };
  }

  subscribeAny(listener: Listener): () => void {
    this.anyListeners.add(listener);
    return () => this.anyListeners.delete(listener);
  }

  getSnapshot(messageId: string): StreamingSnapshot | undefined {
    return this.snapshots.get(messageId);
  }

  getConversationStream(conversationId: string): string | undefined {
    return this.byConversation.get(conversationId);
  }

  getGlobalVersion(): number {
    return this.globalVersion;
  }
}

export const streaming = new StreamingStore();

const noopSubscribe = () => () => {};

/** Subscribes exactly one component to one in-flight message (§6.3 rule). */
export function useStreamingMessage(messageId: string | undefined): StreamingSnapshot | undefined {
  return useSyncExternalStore(
    messageId ? (cb) => streaming.subscribe(messageId, cb) : noopSubscribe,
    () => (messageId ? streaming.getSnapshot(messageId) : undefined),
  );
}

/** Which message (if any) is currently streaming in this conversation. */
export function useConversationStreamId(conversationId: string | undefined): string | undefined {
  return useSyncExternalStore(
    (cb) => streaming.subscribeAny(cb),
    () => (conversationId ? streaming.getConversationStream(conversationId) : undefined),
  );
}
