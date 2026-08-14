"""Job handler registry (§15)."""

from retinue.jobs.handlers.files import extract_file
from retinue.jobs.handlers.memory import memory_extract
from retinue.jobs.handlers.rag import embed_chunks
from retinue.jobs.handlers.titles import generate_title
from retinue.jobs.worker import Handler


def builtin_handlers() -> dict[str, Handler]:
    return {
        "generate_title": generate_title,
        "extract_file": extract_file,
        "embed_chunks": embed_chunks,
        "memory_extract": memory_extract,
    }
