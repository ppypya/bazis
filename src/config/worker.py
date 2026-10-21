import logging

from faststream import FastStream

from application.dtos.events import Destination
from config.ioc.container import Container
from config.settings import Settings
from infrastructures.broker.rabbit import RabbitTopology, create_broker
from presentation.consumers.payments import register_consumer


def create_worker(settings: Settings | None = None) -> FastStream:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level)

    container = Container(settings)
    broker = create_broker(settings.rabbitmq_url)
    topology = RabbitTopology(settings.retry_delay_seconds)

    register_consumer(
        broker, topology.queues[Destination.NEW], topology.exchange, container.process_payment()
    )
    app = FastStream(broker)

    @app.on_startup
    async def prepare() -> None:
        await broker.connect()
        await topology.declare(broker)

    @app.after_shutdown
    async def close_resources() -> None:
        await container.close()

    return app
