import asyncio
import logging
from contextlib import suppress

from faststream import FastStream

from application.dtos.events import Destination
from application.use_cases.publish_outbox import PublishOutbox
from config.ioc.container import Container
from config.settings import Settings
from infrastructures.broker.rabbit import RabbitEventPublisher, RabbitTopology, create_broker
from presentation.consumers.payments import register_consumer
from presentation.jobs.outbox import run_outbox


def create_worker(settings: Settings | None = None) -> FastStream:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level)

    container = Container(settings)
    broker = create_broker(settings.rabbitmq_url)
    topology = RabbitTopology(settings.retry_delay_seconds)
    publisher = PublishOutbox(container.uow, RabbitEventPublisher(broker, topology))

    register_consumer(
        broker, topology.queues[Destination.NEW], topology.exchange, container.process_payment()
    )
    app = FastStream(broker)
    task: asyncio.Task[None] | None = None

    @app.on_startup
    async def prepare() -> None:
        await broker.connect()
        await topology.declare(broker)

    @app.after_startup
    async def start_outbox() -> None:
        nonlocal task

        task = asyncio.create_task(run_outbox(publisher, settings.outbox_poll_seconds))

    @app.on_shutdown
    async def stop_outbox() -> None:
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    @app.after_shutdown
    async def close_resources() -> None:
        await container.close()

    return app
