import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update

from application.dtos.events import Destination
from application.exceptions import IdempotencyConflict, ProcessingBusy, WebhookError
from application.use_cases.create_payment import CreatePayment
from application.use_cases.get_payment import GetPayment
from application.use_cases.process_payment import ProcessPayment
from application.use_cases.publish_outbox import PublishOutbox
from domain.entities.payment import PaymentStatus
from infrastructures.db.models import OutboxModel, PaymentModel

pytestmark = pytest.mark.integration


async def test_concurrent_idempotency_and_atomic_outbox(database, command, webhook_validator):
    engine, uow = database
    create = CreatePayment(uow, webhook_validator)

    results = await asyncio.gather(*(create.execute(command) for _ in range(12)))

    assert len({payment.id for payment in results}) == 1
    async with engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(PaymentModel)) == 1
        assert await connection.scalar(select(func.count()).select_from(OutboxModel)) == 1

    with pytest.raises(IdempotencyConflict):
        await create.execute(replace(command, description="different order"))


async def test_transaction_rolls_back_payment_if_outbox_fails(database, command, webhook_validator):
    engine, factory = database
    real_uow = factory()
    original_enter = real_uow.__aenter__

    class BrokenOutboxUow:
        async def __aenter__(self):
            uow = await original_enter()
            uow.outbox.add = AsyncMock(side_effect=RuntimeError("outbox write failed"))
            return uow

        async def __aexit__(self, *args):
            await real_uow.__aexit__(*args)

    with pytest.raises(RuntimeError):
        await CreatePayment(BrokenOutboxUow, webhook_validator).execute(command)

    async with engine.connect() as connection:
        assert await connection.scalar(select(func.count()).select_from(PaymentModel)) == 0
        assert await connection.scalar(select(func.count()).select_from(OutboxModel)) == 0


async def test_outbox_claim_excludes_other_publishers_and_retries_lost_confirm(
    database, command, webhook_validator
):
    engine, uow = database
    await CreatePayment(uow, webhook_validator).execute(command)
    publisher = AsyncMock()
    publisher.publish.side_effect = [TimeoutError("confirm lost"), None]
    relay = PublishOutbox(uow, publisher)

    assert await relay.execute()
    async with engine.begin() as connection:
        row = (await connection.execute(select(OutboxModel.__table__))).mappings().one()
        assert row["published_at"] is None
        assert row["publish_attempts"] == 1
        await connection.execute(update(OutboxModel).values(available_at=datetime.now(UTC)))

    results = await asyncio.gather(relay.execute(), relay.execute())

    assert sorted(results) == [False, True]
    first, second = publisher.publish.await_args_list
    assert first.args[0].id == second.args[0].id
    async with engine.connect() as connection:
        assert await connection.scalar(select(OutboxModel.published_at)) is not None


async def test_concurrent_delivery_only_one_gateway_call(database, command, webhook_validator):
    _, uow = database
    payment = await CreatePayment(uow, webhook_validator).execute(command)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def charge(payment):
        entered.set()
        await release.wait()
        return PaymentStatus.SUCCEEDED

    gateway = AsyncMock()
    gateway.charge.side_effect = charge
    use_case = ProcessPayment(uow, gateway, AsyncMock())

    first = asyncio.create_task(use_case.execute(payment.id, 1))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(ProcessingBusy):
            await use_case.execute(payment.id, 1)
    finally:
        release.set()
        await first

    gateway.charge.assert_awaited_once()


async def test_retry_state_survives_new_use_case_instances(database, command, webhook_validator):
    engine, uow = database
    payment = await CreatePayment(uow, webhook_validator).execute(command)
    gateway = AsyncMock()
    gateway.charge.return_value = PaymentStatus.SUCCEEDED
    webhook = AsyncMock()
    webhook.send.side_effect = WebhookError("unavailable")

    for attempt in range(1, 4):
        await ProcessPayment(uow, gateway, webhook).execute(payment.id, attempt)

    result = await GetPayment(uow).execute(payment.id)
    assert result.status is PaymentStatus.SUCCEEDED
    assert result.attempts == 3
    assert result.dead_lettered_at is not None
    gateway.charge.assert_awaited_once()

    async with engine.connect() as connection:
        destinations = list(await connection.scalars(select(OutboxModel.destination)))
    assert sorted(destinations) == sorted(
        [Destination.NEW, Destination.RETRY_1, Destination.RETRY_2, Destination.DEAD]
    )
