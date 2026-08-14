"""ORM -> wire model helpers shared by routers."""

from retinue.api.schemas import AttachmentOut, MessageOut, PartOut
from retinue.db.models import Attachment, File, Message, MessagePart


def part_out(part: MessagePart) -> PartOut:
    content = part.content or {}
    if not content and part.text_content:
        content = {"text": part.text_content}
    return PartOut(idx=part.idx, type=part.type, content=content)


def message_out(
    message: Message,
    parts: list[MessagePart],
    attachments: list[tuple[Attachment, File | None]] | None = None,
) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        parent_id=message.parent_id,
        role=message.role,
        status=message.status,
        model=message.model,
        agent_version_id=message.agent_version_id,
        error=message.error,
        created_at=message.created_at,
        parts=[part_out(p) for p in sorted(parts, key=lambda p: p.idx)],
        attachments=[
            AttachmentOut(
                file_id=a.file_id,
                kind=a.kind,
                name=f.original_name if f else None,
                mime=f.mime if f else None,
            )
            for a, f in (attachments or [])
        ],
    )
