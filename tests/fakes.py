from copy import deepcopy
from dataclasses import dataclass, field

from application.dtos.events import OutboxEvent
from domain.entities.payment import Payment


@dataclass
class MemoryStore:
    payments: dict = field(default_factory=dict)
    events: list[OutboxEvent] = field(default_factory=list)

    def uow(self):
        return MemoryUnitOfWork(self)


class MemoryPayments:
    def __init__(self, state):
        self.state = state

    async def get(self, payment_id, *, for_update=False):
        return self.state.payments.get(payment_id)

    async def get_by_key(self, key):
        return next((p for p in self.state.payments.values() if p.idempotency_key == key), None)

    async def insert(self, payment: Payment):
        if await self.get_by_key(payment.idempotency_key):
            return False
        self.state.payments[payment.id] = payment
        return True

    async def save(self, payment):
        self.state.payments[payment.id] = payment


class MemoryOutbox:
    def __init__(self, state):
        self.state = state

    async def add(self, event):
        self.state.events.append(event)


class MemoryUnitOfWork:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        self.state = deepcopy(self.store)
        self.payments = MemoryPayments(self.state)
        self.outbox = MemoryOutbox(self.state)
        return self

    async def __aexit__(self, *args):
        pass

    async def commit(self):
        self.store.payments = deepcopy(self.state.payments)
        self.store.events = deepcopy(self.state.events)
