from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from application.dtos.events import Destination
from application.exceptions import (
    IdempotencyConflict,
    ProcessingBusy,
    WebhookError,
    WebhookRejected,
)
from application.interfaces.adapters import WebhookURLValidator
from application.use_cases.create_payment import CreatePayment
from application.use_cases.process_payment import ProcessPayment
from domain.entities.payment import PaymentStatus


async def test_idempotent_create_and_payload_conflict(store, command, webhook_validator):
    create = CreatePayment(store.uow, webhook_validator)

    first = await create.execute(command)
    second = await create.execute(replace(command, amount=Decimal("123.450")))

    assert first.id == second.id
    assert len(store.events) == 1

    with pytest.raises(IdempotencyConflict):
        await create.execute(replace(command, amount=Decimal("200")))


async def test_rejected_webhook_does_not_open_transaction(command):
    uow_factory = Mock()
    validator = Mock(spec=WebhookURLValidator)
    validator.validate.side_effect = WebhookRejected("Destination forbidden")

    with pytest.raises(WebhookRejected):
        await CreatePayment(uow_factory, validator).execute(command)

    validator.validate.assert_called_once_with(command.webhook_url)
    uow_factory.assert_not_called()


@pytest.mark.parametrize("result", [PaymentStatus.SUCCEEDED, PaymentStatus.FAILED])
async def test_business_result_not_reprocessed_on_duplicate(store, payment, result):
    gateway = AsyncMock()
    gateway.charge.return_value = result
    webhook = AsyncMock()
    process = ProcessPayment(store.uow, gateway, webhook)

    await process.execute(payment.id, 1)
    await process.execute(payment.id, 1)

    saved = store.payments[payment.id]
    assert saved.status is result
    assert saved.webhook_delivered_at is not None
    gateway.charge.assert_awaited_once()
    webhook.send.assert_awaited_once()


async def test_webhook_retry_does_not_charge_again(store, payment):
    gateway = AsyncMock()
    gateway.charge.return_value = PaymentStatus.SUCCEEDED
    webhook = AsyncMock()
    webhook.send.side_effect = [WebhookError(), WebhookError(), None]
    process = ProcessPayment(store.uow, gateway, webhook)

    await process.execute(payment.id, 1)
    assert store.events[-1].destination is Destination.RETRY_1

    await process.execute(payment.id, 1)  # Duplicate before retry is delivered.
    assert webhook.send.await_count == 1

    await process.execute(payment.id, 2)
    assert store.events[-1].destination is Destination.RETRY_2

    await process.execute(payment.id, 3)

    saved = store.payments[payment.id]
    assert saved.attempts == 3
    assert saved.webhook_delivered_at
    assert not saved.dead_lettered_at
    gateway.charge.assert_awaited_once()


async def test_exhaustion_schedules_dlq_atomically(store, payment):
    gateway = AsyncMock()
    gateway.charge.return_value = PaymentStatus.SUCCEEDED
    webhook = AsyncMock()
    webhook.send.side_effect = WebhookError()
    process = ProcessPayment(store.uow, gateway, webhook)

    for attempt in range(1, 4):
        await process.execute(payment.id, attempt)

    saved = store.payments[payment.id]
    assert saved.status is PaymentStatus.SUCCEEDED
    assert saved.dead_lettered_at and not saved.webhook_delivered_at
    assert store.events[-1].destination is Destination.DEAD

    await process.execute(payment.id, 3)
    assert webhook.send.await_count == 3
    gateway.charge.assert_awaited_once()


async def test_active_lease_and_recovery_after_crash(store, payment):
    saved = store.payments[payment.id]
    saved.lock_token = uuid4()
    saved.locked_until = datetime.now(UTC) + timedelta(seconds=60)

    gateway = AsyncMock()
    gateway.charge.return_value = PaymentStatus.SUCCEEDED
    process = ProcessPayment(store.uow, gateway, AsyncMock())

    with pytest.raises(ProcessingBusy):
        await process.execute(payment.id, 1)

    saved.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await process.execute(payment.id, 1)
    assert store.payments[payment.id].webhook_delivered_at
