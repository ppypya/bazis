import logging

from application.use_cases.publish_outbox import PublishOutbox
from config.ioc.container import Container
from config.settings import Settings
from infrastructures.broker.rabbit import RabbitEventPublisher, RabbitTopology, create_broker
from presentation.jobs.outbox import run_outbox

logger = logging.getLogger(__name__)


async def run_outbox_worker(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level)

    container = Container(settings)
    try:
        broker = create_broker(settings.rabbitmq_url)
        topology = RabbitTopology(settings.retry_delay_seconds)
        async with broker:
            await topology.declare(broker)
            publisher = PublishOutbox(container.uow, RabbitEventPublisher(broker, topology))
            logger.info("Outbox worker started")
            await run_outbox(publisher, settings.outbox_poll_seconds)
            raise RuntimeError("Outbox loop stopped unexpectedly")
    finally:
        await container.close()
