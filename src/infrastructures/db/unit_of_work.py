from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructures.db.repositories import SQLOutboxRepository, SQLPaymentRepository


class SQLUnitOfWork:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def __aenter__(self) -> Self:
        self.session = self.sessions()
        self.payments = SQLPaymentRepository(self.session)
        self.outbox = SQLOutboxRepository(self.session)
        return self

    async def __aexit__(self, *args: object) -> None:
        try:
            await self.session.rollback()
        finally:
            await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()
