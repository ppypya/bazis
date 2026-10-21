from decimal import Decimal
from uuid import uuid4

import pytest

from application.dtos.payment import CreatePaymentCommand
from application.use_cases.create_payment import CreatePayment
from domain.value_objects.money import Currency
from infrastructures.webhooks.policy import WebhookURLPolicy
from tests.fakes import MemoryStore


@pytest.fixture
def command():
    return CreatePaymentCommand(
        amount=Decimal("123.45"),
        currency=Currency.RUB,
        description="Order 42",
        metadata={"order_id": 42},
        webhook_url="https://merchant.example/webhook",
        idempotency_key=str(uuid4()),
    )


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def webhook_validator():
    return WebhookURLPolicy()


@pytest.fixture
async def payment(store, command, webhook_validator):
    return await CreatePayment(store.uow, webhook_validator).execute(command)
