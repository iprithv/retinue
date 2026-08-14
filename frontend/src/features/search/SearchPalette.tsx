/** ⌘K palette (§13): search-as-you-type across messages, conversations,
 * files, and agents. */
import * as Dialog from "@radix-ui/react-dialog";
import { FileText, MessageSquare, MessagesSquare, Search, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { SearchHit } from "../../lib/api/types";
import { searchAll } from "../../lib/queries";

const ICONS = {
  message: MessageSquare,
  conversation: MessagesSquare,
  file: FileText,
  agent: Sparkles,
} as const;

export function SearchPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setOpenAndReset = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setQuery("");
      setHits([]);
      setActive(0);
    }
  };

  // re-registered when `open` changes so the handler always sees fresh state
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpenAndReset(!open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const onQueryChange = (value: string) => {
    setQuery(value);
    if (timer.current) clearTimeout(timer.current);
    if (!value.trim()) {
      setHits([]);
      return;
    }
    timer.current = setTimeout(() => {
      void searchAll(value.trim()).then((results) => {
        setHits(results);
        setActive(0);
      });
    }, 150);
  };

  const go = (hit: SearchHit) => {
    setOpen(false);
    if (hit.kind === "agent") navigate(`/agents/${hit.id}`);
    else if (hit.kind === "file") navigate("/files");
    else if (hit.conversation_id) navigate(`/chat/${hit.conversation_id}`);
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpenAndReset}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
        <Dialog.Content className="fixed top-24 left-1/2 z-50 w-[min(40rem,90vw)] -translate-x-1/2 overflow-hidden rounded-2xl border border-line bg-surface-2 shadow-2xl">
          <Dialog.Title className="sr-only">Search</Dialog.Title>
          <div className="flex items-center gap-2 border-b border-line px-4">
            <Search className="size-4 text-muted" />
            <input
              autoFocus
              value={query}
              onChange={(e) => onQueryChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((a) => Math.min(a + 1, hits.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((a) => Math.max(a - 1, 0));
                } else if (e.key === "Enter" && hits[active]) {
                  go(hits[active]);
                }
              }}
              placeholder="Search messages, files, agents…"
              className="flex-1 bg-transparent py-3.5 text-sm outline-none placeholder:text-muted"
            />
            <kbd className="rounded border border-line px-1.5 py-0.5 text-[10px] text-muted">
              esc
            </kbd>
          </div>
          <div className="max-h-96 overflow-y-auto p-1.5">
            {hits.map((hit, i) => {
              const Icon = ICONS[hit.kind];
              return (
                <button
                  key={`${hit.kind}-${hit.id}`}
                  className={`flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left text-sm ${
                    i === active ? "bg-surface-3" : "hover:bg-surface-3"
                  }`}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(hit)}
                >
                  <Icon className="mt-0.5 size-3.5 shrink-0 text-muted" />
                  <span className="min-w-0">
                    <span className="block truncate font-medium">
                      {hit.title ?? "Untitled"}
                    </span>
                    <span className="block truncate text-xs text-muted">{hit.snippet}</span>
                  </span>
                  <span className="ml-auto shrink-0 text-[10px] tracking-wide text-muted uppercase">
                    {hit.kind}
                  </span>
                </button>
              );
            })}
            {query.trim() && hits.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-muted">no results</div>
            ) : null}
            {!query.trim() ? (
              <div className="px-3 py-6 text-center text-xs text-muted">
                Type to search everything — messages, conversations, files, agents.
              </div>
            ) : null}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
