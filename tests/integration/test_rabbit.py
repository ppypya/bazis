import asyncio
import json
import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from application.dtos.events import Destination, OutboxEvent
from infrastructures.broker.rabbit import RabbitEventPublisher, RabbitTopology, create_broker
from presentation.consumers.payments import register_consumer

pytestmark = pytest.mark.integration


async def receive(queue):
    async with asyncio.timeout(8):
        while True:
            message = await queue.get(fail=False)
            if message:
                return message
            await asyncio.sleep(0.05)


async def test_confirms_retry_ttl_and_dlq():
    url = os.getenv("TEST_RABBITMQ_URL")
    if not url:
        pytest.skip("Set TEST_RABBITMQ_URL to run RabbitMQ tests")

    topology = RabbitTopology(retry_delay=0.1, prefix="test." + uuid4().hex)
    broker = create_broker(url)
    async with broker:
        await topology.declare(broker)
        queues = {d: await broker.declare_queue(q) for d, q in topology.queues.items()}
        try:
            publisher = RabbitEventPublisher(broker, topology)
            event = OutboxEvent(
                destination=Destination.RETRY_1,
                payload={"payment_id": str(uuid4()), "attempt": 2},
            )
            await publisher.publish(event)
            message = await receive(queues[Destination.NEW])
            assert json.loads(message.body)["event_id"] == str(event.id)

            await message.reject(requeue=False)
            dead = await receive(queues[Destination.DEAD])
            assert json.loads(dead.body)["event_id"] == str(event.id)
            await dead.ack()

            with pytest.raises(Exception) as error:
                await broker.publish(
                    {}, exchange=topology.exchange, routing_key="unroutable", mandatory=True
                )
            assert "NO_ROUTE" in str(error.value)
        finally:
            for queue in queues.values():
                await queue.delete(if_unused=False, if_empty=False)
            for exchange in (topology.exchange, topology.dead_exchange):
                declared = await broker.declare_exchange(exchange)
                await declared.delete()


async def test_single_consumer_ack_and_malformed_message_rejection():
    url = os.getenv("TEST_RABBITMQ_URL")
    if not url:
        pytest.skip("Set TEST_RABBITMQ_URL to run RabbitMQ tests")

    topology = RabbitTopology(retry_delay=0.1, prefix="test." + uuid4().hex)
    broker = create_broker(url)
    executed = asyncio.Event()
    use_case = AsyncMock()

    async def execute(payment_id, attempt):
        executed.set()

    use_case.execute.side_effect = execute
    register_consumer(broker, topology.queues[Destination.NEW], topology.exchange, use_case)

    async with broker:
        await topology.declare(broker)
        queues = {d: await broker.declare_queue(q) for d, q in topology.queues.items()}
        await broker.start()
        try:
            payment_id = uuid4()
            event = OutboxEvent(
                destination=Destination.NEW,
                payload={"payment_id": str(payment_id), "attempt": 1},
            )
            await RabbitEventPublisher(broker, topology).publish(event)
            await asyncio.wait_for(executed.wait(), timeout=5)
            use_case.execute.assert_awaited_once_with(payment_id, 1)

            await broker.publish(
                b"{invalid json",
                exchange=topology.exchange,
                routing_key=topology.routes[Destination.NEW],
                content_type="application/json",
            )
            dead = await receive(queues[Destination.DEAD])
            assert dead.body == b"{invalid json"
            await dead.ack()
        finally:
            for queue in queues.values():
                await queue.delete(if_unused=False, if_empty=False)
            for exchange in (topology.exchange, topology.dead_exchange):
                declared = await broker.declare_exchange(exchange)
                await declared.delete()
