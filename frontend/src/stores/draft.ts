/** Composer drafts per conversation, persisted (§6.2). */
import { create } from "zustand";
import { persist } from "zustand/middleware";

const NEW_CHAT_KEY = "__new__";

interface DraftState {
  drafts: Record<string, string>;
  get: (conversationId: string | undefined) => string;
  set: (conversationId: string | undefined, text: string) => void;
  clear: (conversationId: string | undefined) => void;
}

export const useDrafts = create<DraftState>()(
  persist(
    (set, get) => ({
      drafts: {},
      get: (conversationId) => get().drafts[conversationId ?? NEW_CHAT_KEY] ?? "",
      set: (conversationId, text) =>
        set((state) => ({
          drafts: { ...state.drafts, [conversationId ?? NEW_CHAT_KEY]: text },
        })),
      clear: (conversationId) =>
        set((state) => {
          const drafts = { ...state.drafts };
          delete drafts[conversationId ?? NEW_CHAT_KEY];
          return { drafts };
        }),
    }),
    { name: "retinue-drafts" },
  ),
);
