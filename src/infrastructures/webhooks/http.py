import httpx

from application.exceptions import WebhookError
from domain.entities.payment import Payment


class HTTPWebhookSender:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def send(self, payment: Payment) -> None:
        payload = {
            "event_id": str(payment.id),
            "event": "payment.completed",
            "payment_id": str(payment.id),
            "status": payment.status.value,
            "amount": str(payment.money.amount),
            "currency": payment.money.currency.value,
            "description": payment.description,
            "metadata": payment.metadata,
            "created_at": payment.created_at.isoformat(),
            "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
        }

        try:
            # Only the status is needed. Close without buffering an untrusted body.
            async with self.client.stream(
                "POST",
                payment.webhook_url,
                json=payload,
                headers={"X-Webhook-Id": str(payment.id)},
                follow_redirects=False,
            ) as response:
                status = response.status_code
        except httpx.HTTPError as exc:
            raise WebhookError("Webhook transport error") from exc

        if not 200 <= status < 300:
            raise WebhookError(f"Webhook returned HTTP {status}")
