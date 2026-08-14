/** Pick an agent for a new conversation (§6.6). The chosen agent's current
 * version is pinned at conversation creation (§9.1). */
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, Sparkles } from "lucide-react";
import { useAgents } from "../../lib/queries";

export function AgentPicker({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (agentId: string | undefined) => void;
}) {
  const { data: agents } = useAgents();
  const selected = agents?.find((a) => a.id === value);
  if (!agents?.length) return null;

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className={`inline-flex max-w-56 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium hover:bg-surface-3 ${
            selected ? "border-accent/50 bg-accent/5 text-accent" : "border-line bg-surface-2 text-ink"
          }`}
        >
          <Sparkles className="size-3.5" />
          <span className="truncate">{selected ? selected.name : "no agent"}</span>
          <ChevronDown className="size-3 text-muted" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={6}
          className="z-50 max-h-80 w-64 overflow-y-auto rounded-xl border border-line bg-surface-2 p-1 shadow-xl"
        >
          <DropdownMenu.Item
            onSelect={() => onChange(undefined)}
            className="flex cursor-pointer items-center justify-between rounded-lg px-2 py-1.5 text-sm outline-none data-highlighted:bg-surface-3"
          >
            <span className="text-muted">No agent (plain chat)</span>
            {!value ? <Check className="size-3.5 text-accent" /> : null}
          </DropdownMenu.Item>
          {agents.map((agent) => (
            <DropdownMenu.Item
              key={agent.id}
              onSelect={() => onChange(agent.id)}
              className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-sm outline-none data-highlighted:bg-surface-3"
            >
              <div className="min-w-0">
                <div className="truncate">{agent.name}</div>
                {agent.description ? (
                  <div className="truncate text-xs text-muted">{agent.description}</div>
                ) : null}
              </div>
              {agent.id === value ? <Check className="size-3.5 shrink-0 text-accent" /> : null}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
