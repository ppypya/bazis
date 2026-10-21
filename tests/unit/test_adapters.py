from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from application.exceptions import WebhookError
from domain.entities.payment import PaymentStatus
from infrastructures.external_apis.gateway import EmulatedPaymentGateway
from infrastructures.webhooks.http import HTTPWebhookSender


async def test_emulator_is_stable_and_has_successes_and_failures(payment):
    gateway = EmulatedPaymentGateway(0, 0)
    results = []

    for number in range(100):
        payment.id = UUID(int=number)
        first = await gateway.charge(payment)
        assert first == await gateway.charge(payment)
        results.append(first)

    assert PaymentStatus.SUCCEEDED in results
    assert PaymentStatus.FAILED in results


async def test_webhook_payload_and_id_are_stable(payment):
    payment.finish(PaymentStatus.SUCCEEDED, datetime.now(UTC))
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        sender = HTTPWebhookSender(client)
        await sender.send(payment)
        await sender.send(payment)

    assert requests[0].content == requests[1].content
    assert requests[0].headers["X-Webhook-Id"] == str(payment.id)


@pytest.mark.parametrize("status", [301, 400, 500])
async def test_non_2xx_webhook_is_an_error(payment, status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(status))
    ) as client:
        with pytest.raises(WebhookError):
            await HTTPWebhookSender(client).send(payment)
