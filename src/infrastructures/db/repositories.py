from dataclasses import asdict
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from application.dtos.events import Destination, OutboxEvent
from domain.entities.payment import Payment
from infrastructures.db.models import OutboxModel, PaymentModel
from infrastructures.mappers.payment import payment_values, to_payment


class SQLPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, payment_id: UUID, *, for_update: bool = False) -> Payment | None:
        statement = select(PaymentModel).where(PaymentModel.id == payment_id)
        if for_update:
            statement = statement.with_for_update()

        row = await self.session.scalar(statement)
        return to_payment(row) if row else None

    async def get_by_key(self, key: str) -> Payment | None:
        row = await self.session.scalar(
            select(PaymentModel).where(PaymentModel.idempotency_key == key)
        )
        return to_payment(row) if row else None

    async def insert(self, payment: Payment) -> bool:
        statement = (
            insert(PaymentModel.__table__)
            .values(**payment_values(payment))
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(PaymentModel.id)
        )

        return (await self.session.scalar(statement)) is not None

    async def save(self, payment: Payment) -> None:
        await self.session.execute(
            update(PaymentModel.__table__)
            .where(PaymentModel.id == payment.id)
            .values(**payment_values(payment))
        )


class SQLOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, event: OutboxEvent) -> None:
        self.session.add(OutboxModel(**asdict(event)))

    async def claim(self, now: datetime, lease_seconds: float) -> OutboxEvent | None:
        row = await self.session.scalar(
            select(OutboxModel)
            .where(
                OutboxModel.published_at.is_(None),
                OutboxModel.available_at <= now,
                or_(OutboxModel.locked_until.is_(None), OutboxModel.locked_until <= now),
            )
            .order_by(OutboxModel.available_at, OutboxModel.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None

        row.lock_token = uuid4()
        row.locked_until = now + timedelta(seconds=lease_seconds)

        return OutboxEvent(
            id=row.id,
            destination=Destination(row.destination),
            payload=row.payload,
            created_at=row.created_at,
            published_at=row.published_at,
            publish_attempts=row.publish_attempts,
            available_at=row.available_at,
            last_error=row.last_error,
            lock_token=row.lock_token,
            locked_until=row.locked_until,
        )

    async def mark_published(self, event: OutboxEvent, now: datetime) -> None:
        await self.session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == event.id, OutboxModel.lock_token == event.lock_token)
            .values(published_at=now, lock_token=None, locked_until=None, last_error=None)
        )

    async def mark_failed(self, event: OutboxEvent, now: datetime, error: str) -> None:
        delay = min(60, 2 ** min(event.publish_attempts, 6))

        await self.session.execute(
            update(OutboxModel)
            .where(OutboxModel.id == event.id, OutboxModel.lock_token == event.lock_token)
            .values(
                publish_attempts=event.publish_attempts + 1,
                available_at=now + timedelta(seconds=delay),
                last_error=error,
                lock_token=None,
                locked_until=None,
            )
        )
