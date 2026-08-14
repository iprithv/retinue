/** Shiki-in-a-Worker bridge (§6.4): highlighting never blocks a paint.
 * Failures degrade silently to the plain <pre> that already rendered. */

interface Pending {
  resolve: (html: string | null) => void;
}

let worker: Worker | null | undefined;
let seq = 0;
const pending = new Map<number, Pending>();

function getWorker(): Worker | null {
  if (worker !== undefined) return worker;
  try {
    worker = new Worker(new URL("../../workers/highlight.worker.ts", import.meta.url), {
      type: "module",
    });
    worker.onmessage = (event: MessageEvent<{ id: number; html: string | null }>) => {
      pending.get(event.data.id)?.resolve(event.data.html);
      pending.delete(event.data.id);
    };
    worker.onerror = () => {
      for (const entry of pending.values()) entry.resolve(null);
      pending.clear();
    };
  } catch {
    worker = null;
  }
  return worker;
}

export function requestHighlight(code: string, language: string): Promise<string | null> {
  const w = getWorker();
  if (!w || code.length > 50_000) return Promise.resolve(null);
  const id = ++seq;
  return new Promise((resolve) => {
    pending.set(id, { resolve });
    w.postMessage({ id, code, language });
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id);
        resolve(null);
      }
    }, 5000);
  });
}
