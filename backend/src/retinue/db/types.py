"""Portable column types (§17): UUIDv7 as 16-byte BLOB (SQLite) / uuid (PG),
JSON as TEXT (SQLite) / JSONB (PG)."""

import uuid
from typing import Any

from sqlalchemy import JSON, LargeBinary
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine


class UUIDBlob(TypeDecorator[uuid.UUID]):
    impl = LargeBinary(16)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(LargeBinary(16))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        if dialect.name == "postgresql":
            return value
        return value.bytes

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(bytes=value)


# TEXT-backed JSON on SQLite, JSONB on Postgres (serialization is orjson via
# the engine's json_serializer/json_deserializer).
JSONVal = JSON().with_variant(JSONB(), "postgresql")
