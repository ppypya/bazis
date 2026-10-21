import logging
from datetime import UTC, datetime

from application.interfaces.adapters import EventPublisher
from application.interfaces.unit_of_work import UnitOfWorkFactory

logger = logging.getLogger(__name__)


class PublishOutbox:
    def __init__(self, uow_factory: UnitOfWorkFactory, publisher: EventPublisher) -> None:
        self.uow_factory = uow_factory
        self.publisher = publisher

    async def execute(self) -> bool:
        async with self.uow_factory() as uow:
            event = await uow.outbox.claim(datetime.now(UTC), lease_seconds=30)
            await uow.commit()

        if event is None:
            return False

        try:
            await self.publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "Outbox publish failed event_id=%s error=%s", event.id, type(exc).__name__
            )
            async with self.uow_factory() as uow:
                await uow.outbox.mark_failed(event, datetime.now(UTC), type(exc).__name__)
                await uow.commit()
        else:
            async with self.uow_factory() as uow:
                await uow.outbox.mark_published(event, datetime.now(UTC))
                await uow.commit()

        return True
