from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class Destination(StrEnum):
    NEW = "new"
    RETRY_1 = "retry_1"
    RETRY_2 = "retry_2"
    DEAD = "dead"


@dataclass
class OutboxEvent:
    destination: Destination
    payload: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    publish_attempts: int = 0
    available_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_error: str | None = None
    lock_token: UUID | None = None
    locked_until: datetime | None = None
