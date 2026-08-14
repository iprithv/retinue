"""ORM -> wire model helpers shared by routers."""

from retinue.api.schemas import MessageOut, PartOut
from retinue.db.models import Message, MessagePart


def part_out(part: MessagePart) -> PartOut:
    content = part.content or {}
    if not content and part.text_content:
        content = {"text": part.text_content}
    return PartOut(idx=part.idx, type=part.type, content=content)


def message_out(message: Message, parts: list[MessagePart]) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        parent_id=message.parent_id,
        role=message.role,
        status=message.status,
        model=message.model,
        error=message.error,
        created_at=message.created_at,
        parts=[part_out(p) for p in sorted(parts, key=lambda p: p.idx)],
    )
