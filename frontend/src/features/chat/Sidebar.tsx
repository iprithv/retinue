import {
  FolderOpen,
  MoreHorizontal,
  Pin,
  PinOff,
  Plus,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  SquarePen,
  Trash2,
} from "lucide-react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { Conversation } from "../../lib/api/types";
import { useConversations, useDeleteConversation, usePatchConversation } from "../../lib/queries";
import { useAuth } from "../../stores/auth";

function groupLabel(conversation: Conversation): string {
  const ts = conversation.last_message_at ?? conversation.created_at;
  const days = Math.floor((Date.now() - ts) / 86_400_000);
  if (conversation.pinned) return "Pinned";
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  return "Older";
}

const GROUP_ORDER = ["Pinned", "Today", "Yesterday", "Previous 7 days", "Older"];

export function Sidebar() {
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const { data: conversations } = useConversations();
  const patch = usePatchConversation();
  const remove = useDeleteConversation();
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const groups = new Map<string, Conversation[]>();
  for (const conversation of conversations ?? []) {
    const label = groupLabel(conversation);
    const bucket = groups.get(label) ?? [];
    bucket.push(conversation);
    groups.set(label, bucket);
  }

  const commitRename = (conversation: Conversation) => {
    const title = renameValue.trim();
    setRenaming(null);
    if (title && title !== conversation.title) {
      patch.mutate({ id: conversation.id, patch: { title } });
    }
  };

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-line bg-surface-2">
      <div className="flex items-center justify-between p-3">
        <Link to="/" className="flex items-center gap-2 px-1 font-semibold tracking-tight">
          <span className="text-lg">⚜️</span> retinue
        </Link>
        <Link
          to="/"
          title="New chat"
          className="rounded-lg p-2 text-muted hover:bg-surface-3 hover:text-ink"
        >
          <SquarePen className="size-4" />
        </Link>
      </div>

      <div className="space-y-0.5 px-2 pb-1">
        <button
          onClick={() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }))}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-ink"
        >
          <Search className="size-4" /> Search
          <kbd className="ml-auto rounded border border-line px-1 text-[10px]">⌘K</kbd>
        </button>
        <Link
          to="/agents"
          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-ink"
        >
          <Sparkles className="size-4" /> Agents
        </Link>
        <Link
          to="/files"
          className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-ink"
        >
          <FolderOpen className="size-4" /> Files
        </Link>
        {user?.role === "owner" || user?.role === "admin" ? (
          <Link
            to="/admin"
            className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted hover:bg-surface-3 hover:text-ink"
          >
            <ShieldCheck className="size-4" /> Admin
          </Link>
        ) : null}
      </div>

      <nav className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {(conversations?.length ?? 0) === 0 ? (
          <button
            onClick={() => navigate("/")}
            className="mt-2 flex w-full items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2 text-sm text-muted hover:bg-surface-3"
          >
            <Plus className="size-4" /> Start your first chat
          </button>
        ) : null}
        {GROUP_ORDER.filter((label) => groups.has(label)).map((label) => (
          <div key={label} className="mb-2">
            <div className="px-2 pt-3 pb-1 text-[10px] font-semibold tracking-wider text-muted uppercase">
              {label}
            </div>
            {(groups.get(label) ?? []).map((conversation) => {
              const active = conversation.id === conversationId;
              return (
                <div
                  key={conversation.id}
                  className={`group relative flex items-center rounded-lg text-sm ${
                    active ? "bg-surface-3" : "hover:bg-surface-3/60"
                  }`}
                >
                  {renaming === conversation.id ? (
                    <input
                      autoFocus
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onBlur={() => commitRename(conversation)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename(conversation);
                        if (e.key === "Escape") setRenaming(null);
                      }}
                      className="m-1 w-full rounded-md border border-accent bg-surface-2 px-2 py-1 text-sm outline-none"
                    />
                  ) : (
                    <>
                      <Link
                        to={`/chat/${conversation.id}`}
                        className="min-w-0 flex-1 truncate px-3 py-2"
                        title={conversation.title ?? "New conversation"}
                      >
                        {conversation.title ?? (
                          <span className="text-muted italic">New conversation</span>
                        )}
                      </Link>
                      <DropdownMenu.Root>
                        <DropdownMenu.Trigger asChild>
                          <button className="mr-1 rounded-md p-1 text-muted opacity-0 group-hover:opacity-100 hover:bg-surface-2 data-[state=open]:opacity-100">
                            <MoreHorizontal className="size-4" />
                          </button>
                        </DropdownMenu.Trigger>
                        <DropdownMenu.Portal>
                          <DropdownMenu.Content
                            align="start"
                            className="z-50 w-44 rounded-xl border border-line bg-surface-2 p-1 shadow-xl"
                          >
                            <DropdownMenu.Item
                              onSelect={() => {
                                setRenaming(conversation.id);
                                setRenameValue(conversation.title ?? "");
                              }}
                              className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm outline-none data-highlighted:bg-surface-3"
                            >
                              <SquarePen className="size-3.5" /> Rename
                            </DropdownMenu.Item>
                            <DropdownMenu.Item
                              onSelect={() =>
                                patch.mutate({
                                  id: conversation.id,
                                  patch: { pinned: !conversation.pinned },
                                })
                              }
                              className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm outline-none data-highlighted:bg-surface-3"
                            >
                              {conversation.pinned ? (
                                <>
                                  <PinOff className="size-3.5" /> Unpin
                                </>
                              ) : (
                                <>
                                  <Pin className="size-3.5" /> Pin
                                </>
                              )}
                            </DropdownMenu.Item>
                            <DropdownMenu.Item
                              onSelect={() => {
                                remove.mutate(conversation.id);
                                if (active) navigate("/");
                              }}
                              className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-danger outline-none data-highlighted:bg-surface-3"
                            >
                              <Trash2 className="size-3.5" /> Delete
                            </DropdownMenu.Item>
                          </DropdownMenu.Content>
                        </DropdownMenu.Portal>
                      </DropdownMenu.Root>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="border-t border-line p-2">
        <div className="flex items-center justify-between rounded-lg px-2 py-1.5">
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">{user?.name || user?.email}</div>
            <div className="text-[10px] text-muted uppercase">{user?.role}</div>
          </div>
          <div className="flex items-center gap-0.5">
            <Link
              to="/settings"
              title="Settings"
              className="rounded-lg p-2 text-muted hover:bg-surface-3 hover:text-ink"
            >
              <Settings className="size-4" />
            </Link>
            <button
              onClick={() => void logout().then(() => navigate("/login"))}
              title="Sign out"
              className="rounded-lg p-2 text-xs text-muted hover:bg-surface-3 hover:text-ink"
            >
              ↩
            </button>
          </div>
        </div>
      </div>
    </aside>
  );
}
