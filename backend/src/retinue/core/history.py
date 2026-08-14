"""Branch-tree helpers (§17 branching semantics).

The visible thread of a conversation is the path from root following, at each
fork, the latest-created child. Editing or regenerating creates siblings; the
new node becomes the visible branch because it is newest.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from retinue.core.assembly import HistoryEntry
from retinue.db.models import Message, MessagePart


@dataclass(slots=True)
class ThreadMessage:
    message: Message
    parts: list[MessagePart]

    @property
    def text(self) -> str:
        return "\n\n".join(
            p.text_content or "" for p in self.parts if p.type == "text" and p.text_content
        )


async def load_thread(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    leaf_id: uuid.UUID | None = None,
) -> list[ThreadMessage]:
    """Messages on the active branch, chronological. leaf_id pins a branch."""
    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    if not messages:
        return []

    by_id = {m.id: m for m in messages}
    if leaf_id is not None and leaf_id in by_id:
        leaf = by_id[leaf_id]
    else:
        leaf = messages[-1]  # newest node is the active branch tip

    path: list[Message] = []
    node: Message | None = leaf
    while node is not None:
        path.append(node)
        node = by_id.get(node.parent_id) if node.parent_id else None
    path.reverse()

    part_rows = (
        (
            await session.execute(
                select(MessagePart)
                .where(MessagePart.message_id.in_([m.id for m in path]))
                .order_by(MessagePart.idx)
            )
        )
        .scalars()
        .all()
    )
    parts_by_message: dict[uuid.UUID, list[MessagePart]] = {}
    for part in part_rows:
        parts_by_message.setdefault(part.message_id, []).append(part)

    return [ThreadMessage(message=m, parts=parts_by_message.get(m.id, [])) for m in path]


def to_history(thread: list[ThreadMessage]) -> list[HistoryEntry]:
    """Provider-facing history: user turns plus assistant turns that produced text.

    Errored or empty assistant turns are skipped — the model should not see
    half-broken context. Stopped-but-partial turns stay (the user saw them).
    """
    entries: list[HistoryEntry] = []
    for tm in thread:
        if tm.message.role == "user":
            entries.append(HistoryEntry(role="user", text=tm.text))
        elif tm.message.role == "assistant":
            if tm.message.status in ("complete", "stopped") and tm.text:
                entries.append(HistoryEntry(role="assistant", text=tm.text))
    return entries
