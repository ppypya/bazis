from typing import Any

from domain.entities.payment import Payment, PaymentStatus
from domain.value_objects.money import Currency, Money
from infrastructures.db.models import PaymentModel


def to_payment(row: PaymentModel) -> Payment:
    return Payment(
        id=row.id,
        money=Money(row.amount, Currency(row.currency)),
        description=row.description,
        metadata=row.metadata_,
        idempotency_key=row.idempotency_key,
        request_hash=row.request_hash,
        webhook_url=row.webhook_url,
        created_at=row.created_at,
        status=PaymentStatus(row.status),
        processed_at=row.processed_at,
        webhook_delivered_at=row.webhook_delivered_at,
        attempts=row.attempts,
        last_error=row.last_error,
        dead_lettered_at=row.dead_lettered_at,
        lock_token=row.lock_token,
        locked_until=row.locked_until,
    )


def payment_values(payment: Payment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "amount": payment.money.amount,
        "currency": payment.money.currency.value,
        "description": payment.description,
        "metadata": payment.metadata,
        "idempotency_key": payment.idempotency_key,
        "request_hash": payment.request_hash,
        "webhook_url": payment.webhook_url,
        "created_at": payment.created_at,
        "status": payment.status.value,
        "processed_at": payment.processed_at,
        "webhook_delivered_at": payment.webhook_delivered_at,
        "attempts": payment.attempts,
        "last_error": payment.last_error,
        "dead_lettered_at": payment.dead_lettered_at,
        "lock_token": payment.lock_token,
        "locked_until": payment.locked_until,
    }
