from typing import Protocol

from application.dtos.events import OutboxEvent
from domain.entities.payment import Payment, PaymentStatus


class PaymentGateway(Protocol):
    async def charge(self, payment: Payment) -> PaymentStatus: ...


class WebhookSender(Protocol):
    async def send(self, payment: Payment) -> None: ...


class WebhookURLValidator(Protocol):
    def validate(self, url: str) -> None: ...


class EventPublisher(Protocol):
    async def publish(self, event: OutboxEvent) -> None: ...
