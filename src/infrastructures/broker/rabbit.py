from faststream.rabbit import Channel, QueueType, RabbitBroker, RabbitExchange, RabbitQueue
from faststream.rabbit.schemas.queue import QuorumQueueArgs

from application.dtos.events import Destination, OutboxEvent


class RabbitTopology:
    def __init__(self, retry_delay: float = 5, prefix: str = "payments") -> None:
        self.exchange = RabbitExchange(f"{prefix}.events", durable=True)
        self.dead_exchange = RabbitExchange(f"{prefix}.dead", durable=True)

        self.routes = {
            Destination.NEW: f"{prefix}.new",
            Destination.RETRY_1: f"{prefix}.retry.1",
            Destination.RETRY_2: f"{prefix}.retry.2",
            Destination.DEAD: f"{prefix}.dlq",
        }

        self.queues = {
            Destination.DEAD: RabbitQueue(
                self.routes[Destination.DEAD], queue_type=QueueType.QUORUM, durable=True
            )
        }
        for destination in (Destination.NEW, Destination.RETRY_1, Destination.RETRY_2):
            arguments: QuorumQueueArgs = {
                "x-dead-letter-exchange": (
                    self.dead_exchange.name
                    if destination is Destination.NEW
                    else self.exchange.name
                ),
                "x-dead-letter-routing-key": (
                    self.routes[Destination.DEAD]
                    if destination is Destination.NEW
                    else self.routes[Destination.NEW]
                ),
                "x-dead-letter-strategy": "at-least-once",
                "x-overflow": "reject-publish",
                # Application counts attempts in PostgreSQL. Keep the broker guard
                # high so transient DB outages do not consume the three attempts.
                # pamqp 3.3.0 cannot encode the -1 ("unlimited") AMQP argument.
                "x-delivery-limit": 2_147_483_647,
            }
            if destination is not Destination.NEW:
                multiplier = 1 if destination is Destination.RETRY_1 else 2
                arguments["x-message-ttl"] = int(retry_delay * multiplier * 1000)

            self.queues[destination] = RabbitQueue(
                self.routes[destination],
                queue_type=QueueType.QUORUM,
                durable=True,
                arguments=arguments,
            )

    async def declare(self, broker: RabbitBroker) -> None:
        exchange = await broker.declare_exchange(self.exchange)
        dead_exchange = await broker.declare_exchange(self.dead_exchange)

        for destination, specification in self.queues.items():
            queue = await broker.declare_queue(specification)
            target = dead_exchange if destination is Destination.DEAD else exchange
            await queue.bind(target, routing_key=self.routes[destination])


def create_broker(url: str) -> RabbitBroker:
    return RabbitBroker(
        url,
        default_channel=Channel(prefetch_count=1, publisher_confirms=True, on_return_raises=True),
        graceful_timeout=50,
    )


class RabbitEventPublisher:
    def __init__(self, broker: RabbitBroker, topology: RabbitTopology) -> None:
        self.broker = broker
        self.topology = topology

    async def publish(self, event: OutboxEvent) -> None:
        exchange = (
            self.topology.dead_exchange
            if event.destination is Destination.DEAD
            else self.topology.exchange
        )

        await self.broker.publish(
            {**event.payload, "event_id": str(event.id)},
            exchange=exchange,
            routing_key=self.topology.routes[event.destination],
            message_id=str(event.id),
            persist=True,
            mandatory=True,
            timeout=5,
        )
