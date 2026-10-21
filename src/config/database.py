from pydantic import BaseModel


class DatabaseSettings(BaseModel):
    database_url: str = "postgresql+asyncpg://payments:payments@localhost:55432/payments"
