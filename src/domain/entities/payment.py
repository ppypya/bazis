from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from domain.exceptions import DomainError
from domain.value_objects.money import Money


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class Payment:
    id: UUID
    money: Money
    description: str
    metadata: dict[str, Any]
    idempotency_key: str
    request_hash: str
    webhook_url: str
    created_at: datetime
    status: PaymentStatus = PaymentStatus.PENDING
    processed_at: datetime | None = None
    webhook_delivered_at: datetime | None = None
    attempts: int = 0
    last_error: str | None = None
    dead_lettered_at: datetime | None = None
    lock_token: UUID | None = None
    locked_until: datetime | None = None

    def finish(self, status: PaymentStatus, now: datetime) -> None:
        if status is PaymentStatus.PENDING:
            raise DomainError("A payment result must be final")
        if self.status is not PaymentStatus.PENDING:
            raise DomainError("Payment has already been processed")

        self.status = status
        self.processed_at = now
