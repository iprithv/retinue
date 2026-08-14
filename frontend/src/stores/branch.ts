/** Per-conversation selected-child map for fork points (§17). Session-scoped
 * on purpose: the default view (newest branch) is always recoverable. */
import { create } from "zustand";

interface BranchState {
  selections: Record<string, Record<string, string>>; // conversationId -> parentKey -> childId
  select: (conversationId: string, parentKey: string, childId: string) => void;
}

export const useBranches = create<BranchState>((set) => ({
  selections: {},
  select: (conversationId, parentKey, childId) =>
    set((state) => ({
      selections: {
        ...state.selections,
        [conversationId]: {
          ...state.selections[conversationId],
          [parentKey]: childId,
        },
      },
    })),
}));
