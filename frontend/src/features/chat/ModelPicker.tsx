import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, Cpu } from "lucide-react";
import { useModels, usePatchMe } from "../../lib/queries";
import { useAuth } from "../../stores/auth";

export function ModelPicker({
  value,
  onChange,
}: {
  value: string | undefined;
  onChange: (model: string) => void;
}) {
  const { data: models } = useModels();
  const patchMe = usePatchMe();
  const setUser = useAuth((s) => s.setUser);

  const grouped = new Map<string, typeof models>();
  for (const model of models ?? []) {
    const bucket = grouped.get(model.provider) ?? [];
    bucket.push(model);
    grouped.set(model.provider, bucket);
  }
  const currentLabel = value ? (value.split("/")[1] ?? value) : "choose model";

  const select = (id: string) => {
    onChange(id);
    // remember as the user default (fire-and-forget)
    patchMe.mutate(
      { settings: { default_model: id } },
      { onSuccess: (user) => setUser(user) },
    );
  };

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          className="inline-flex max-w-64 items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-2.5 py-1.5 text-xs font-medium text-ink hover:bg-surface-3"
          title={value}
        >
          <Cpu className="size-3.5 text-muted" />
          <span className="truncate">{currentLabel}</span>
          <ChevronDown className="size-3 text-muted" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={6}
          className="z-50 max-h-96 w-72 overflow-y-auto rounded-xl border border-line bg-surface-2 p-1 shadow-xl"
        >
          {[...grouped.entries()].map(([provider, providerModels]) => (
            <DropdownMenu.Group key={provider}>
              <DropdownMenu.Label className="px-2 pt-2 pb-1 text-[10px] font-semibold tracking-wider text-muted uppercase">
                {provider}
              </DropdownMenu.Label>
              {(providerModels ?? []).map((model) => (
                <DropdownMenu.Item
                  key={model.id}
                  onSelect={() => select(model.id)}
                  className="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-sm outline-none data-highlighted:bg-surface-3"
                >
                  <span className="truncate">{model.display_name}</span>
                  {model.id === value ? <Check className="size-3.5 text-accent" /> : null}
                </DropdownMenu.Item>
              ))}
            </DropdownMenu.Group>
          ))}
          {(models?.length ?? 0) === 0 ? (
            <div className="p-3 text-xs text-muted">
              No models available. Add a provider key in Settings.
            </div>
          ) : null}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
