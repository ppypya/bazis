from uuid import UUID

from application.exceptions import PaymentNotFound
from application.interfaces.unit_of_work import UnitOfWorkFactory
from domain.entities.payment import Payment


class GetPayment:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self.uow_factory = uow_factory

    async def execute(self, payment_id: UUID) -> Payment:
        async with self.uow_factory() as uow:
            payment = await uow.payments.get(payment_id)

        if payment is None:
            raise PaymentNotFound

        return payment
