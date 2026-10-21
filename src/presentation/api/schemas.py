import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Self
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from application.dtos.payment import CreatePaymentCommand
from domain.entities.payment import Payment, PaymentStatus
from domain.value_objects.money import Currency


class CreatePaymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
    currency: Currency
    description: str = Field(default="", max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: AnyHttpUrl = Field(max_length=2048)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if any(
            value is not None
            for value in (
                self.webhook_url.username, self.webhook_url.password, self.webhook_url.fragment
            )
        ):
            raise ValueError("Webhook URL must not contain credentials or a fragment")

        try:
            encoded = json.dumps(self.metadata, allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("Metadata must be valid JSON") from exc
        if len(encoded) > 16384:
            raise ValueError("Metadata must not exceed 16 KiB")

        return self

    def to_command(self, key: str) -> CreatePaymentCommand:
        return CreatePaymentCommand(
            amount=self.amount,
            currency=self.currency,
            description=self.description,
            metadata=self.metadata,
            webhook_url=str(self.webhook_url),
            idempotency_key=key,
        )


class PaymentAccepted(BaseModel):
    payment_id: UUID
    status: PaymentStatus
    created_at: datetime


class PaymentResponse(PaymentAccepted):
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    idempotency_key: str
    webhook_url: str
    processed_at: datetime | None
    webhook_delivered_at: datetime | None
    attempts: int
    last_error: str | None
    dead_lettered_at: datetime | None

    @classmethod
    def from_payment(cls, payment: Payment) -> Self:
        return cls(
            payment_id=payment.id,
            status=payment.status,
            created_at=payment.created_at,
            amount=payment.money.amount,
            currency=payment.money.currency,
            description=payment.description,
            metadata=payment.metadata,
            idempotency_key=payment.idempotency_key,
            webhook_url=payment.webhook_url,
            processed_at=payment.processed_at,
            webhook_delivered_at=payment.webhook_delivered_at,
            attempts=payment.attempts,
            last_error=payment.last_error,
            dead_lettered_at=payment.dead_lettered_at,
        )
