import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from application.use_cases.create_payment import CreatePayment
from application.use_cases.get_payment import GetPayment
from application.use_cases.process_payment import ProcessPayment
from config.settings import Settings
from infrastructures.db.unit_of_work import SQLUnitOfWork
from infrastructures.external_apis.gateway import EmulatedPaymentGateway
from infrastructures.webhooks.http import HTTPWebhookSender
from infrastructures.webhooks.policy import WebhookURLPolicy
from infrastructures.webhooks.transport import SafeWebhookTransport


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        self.engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"server_settings": {"statement_timeout": "10000"}},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        self.webhook_policy = WebhookURLPolicy(allow_local=settings.webhook_allow_local)
        self.http = httpx.AsyncClient(
            timeout=settings.webhook_timeout_seconds,
            trust_env=False,
            transport=SafeWebhookTransport(self.webhook_policy),
        )

        self.gateway = EmulatedPaymentGateway(
            settings.gateway_min_delay_seconds, settings.gateway_max_delay_seconds
        )
        self.webhook = HTTPWebhookSender(self.http)

    def uow(self) -> SQLUnitOfWork:
        return SQLUnitOfWork(self.sessions)

    def create_payment(self) -> CreatePayment:
        return CreatePayment(self.uow, self.webhook_policy)

    def get_payment(self) -> GetPayment:
        return GetPayment(self.uow)

    def process_payment(self) -> ProcessPayment:
        return ProcessPayment(
            self.uow,
            self.gateway,
            self.webhook,
            lease_seconds=self.settings.processing_lease_seconds,
            operation_timeout=self.settings.operation_timeout_seconds,
        )

    async def close(self) -> None:
        try:
            await self.http.aclose()
        finally:
            await self.engine.dispose()
