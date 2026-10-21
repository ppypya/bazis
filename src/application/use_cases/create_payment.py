from datetime import UTC, datetime
from uuid import uuid4

from application.dtos.events import Destination, OutboxEvent
from application.dtos.payment import CreatePaymentCommand
from application.exceptions import IdempotencyConflict
from application.interfaces.adapters import WebhookURLValidator
from application.interfaces.unit_of_work import UnitOfWorkFactory
from domain.entities.payment import Payment
from domain.value_objects.money import Money


class CreatePayment:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, webhook_validator: WebhookURLValidator
    ) -> None:
        self.uow_factory = uow_factory
        self.webhook_validator = webhook_validator

    async def execute(self, command: CreatePaymentCommand) -> Payment:
        self.webhook_validator.validate(command.webhook_url)
        payment = Payment(
            id=uuid4(),
            money=Money(command.amount, command.currency),
            description=command.description,
            metadata=command.metadata,
            idempotency_key=command.idempotency_key,
            request_hash=command.fingerprint(),
            webhook_url=command.webhook_url,
            created_at=datetime.now(UTC),
        )

        async with self.uow_factory() as uow:
            # INSERT ON CONFLICT handles concurrent requests with the same key.
            if not await uow.payments.insert(payment):
                existing = await uow.payments.get_by_key(command.idempotency_key)
                if existing is None or existing.request_hash != payment.request_hash:
                    raise IdempotencyConflict
                return existing

            await uow.outbox.add(
                OutboxEvent(
                    destination=Destination.NEW,
                    payload={"payment_id": str(payment.id), "attempt": 1},
                )
            )
            await uow.commit()

        return payment
