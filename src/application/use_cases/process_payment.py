import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from application.dtos.events import Destination, OutboxEvent
from application.exceptions import LostLease, PaymentNotFound, ProcessingBusy
from application.interfaces.adapters import PaymentGateway, WebhookSender
from application.interfaces.unit_of_work import UnitOfWorkFactory
from domain.entities.payment import Payment, PaymentStatus

MAX_ATTEMPTS = 3


class ProcessPayment:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        gateway: PaymentGateway,
        webhook: WebhookSender,
        *,
        lease_seconds: float = 90,
        operation_timeout: float = 20,
    ) -> None:
        self.uow_factory = uow_factory
        self.gateway = gateway
        self.webhook = webhook
        self.lease_seconds = lease_seconds
        self.operation_timeout = operation_timeout

    async def execute(self, payment_id: UUID, attempt: int) -> None:
        payment = await self._claim(payment_id, attempt)
        if payment is None:
            return

        stage = "gateway"
        try:
            if payment.status is PaymentStatus.PENDING:
                async with asyncio.timeout(self.operation_timeout):
                    result = await self.gateway.charge(payment)

                async with self.uow_factory() as uow:
                    current = await uow.payments.get(payment.id, for_update=True)
                    current = self._require_owner(current, payment)
                    current.finish(result, datetime.now(UTC))
                    await uow.payments.save(current)
                    await uow.commit()
                    payment = current

            stage = "webhook"
            async with asyncio.timeout(self.operation_timeout):
                await self.webhook.send(payment)
        except LostLease:
            raise
        except Exception as exc:
            await self._record_failure(payment, attempt, stage, type(exc).__name__)
            return

        async with self.uow_factory() as uow:
            current = await uow.payments.get(payment.id, for_update=True)
            current = self._require_owner(current, payment)

            current.webhook_delivered_at = datetime.now(UTC)
            current.attempts = attempt
            current.last_error = None
            current.lock_token = None
            current.locked_until = None

            await uow.payments.save(current)
            await uow.commit()

    async def _claim(self, payment_id: UUID, attempt: int) -> Payment | None:
        async with self.uow_factory() as uow:
            payment = await uow.payments.get(payment_id, for_update=True)
            if payment is None:
                raise PaymentNotFound

            if payment.webhook_delivered_at or payment.dead_lettered_at:
                return None
            if attempt <= payment.attempts:
                return None  # Duplicate of an already durably scheduled attempt.
            if attempt != payment.attempts + 1 or not 1 <= attempt <= MAX_ATTEMPTS:
                raise ValueError("Unexpected processing attempt")

            now = datetime.now(UTC)
            if payment.locked_until and payment.locked_until > now:
                raise ProcessingBusy

            payment.lock_token = uuid4()
            payment.locked_until = now + timedelta(seconds=self.lease_seconds)

            await uow.payments.save(payment)
            await uow.commit()

            return payment

    @staticmethod
    def _require_owner(current: Payment | None, owner: Payment) -> Payment:
        if current is None or current.lock_token != owner.lock_token:
            raise LostLease

        return current

    async def _record_failure(self, payment: Payment, attempt: int, stage: str, error: str) -> None:
        async with self.uow_factory() as uow:
            current = await uow.payments.get(payment.id, for_update=True)
            current = self._require_owner(current, payment)

            now = datetime.now(UTC)
            current.attempts = attempt
            current.last_error = f"{stage}: {error}"
            current.lock_token = None
            current.locked_until = None

            if attempt == MAX_ATTEMPTS:
                current.dead_lettered_at = now
                if current.status is PaymentStatus.PENDING:
                    current.finish(PaymentStatus.FAILED, now)
                destination = Destination.DEAD
                next_attempt = attempt
            else:
                destination = Destination.RETRY_1 if attempt == 1 else Destination.RETRY_2
                next_attempt = attempt + 1

            await uow.payments.save(current)
            await uow.outbox.add(
                OutboxEvent(
                    destination=destination,
                    payload={
                        "payment_id": str(current.id),
                        "attempt": next_attempt,
                        "last_error": current.last_error,
                    },
                )
            )
            await uow.commit()
