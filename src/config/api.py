import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.ioc.container import Container
from config.settings import Settings
from presentation.api.controllers import router
from presentation.api.dependencies import APIDependencies, require_api_key


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        container = Container(settings)
        try:
            app.state.dependencies = APIDependencies(
                api_key=settings.api_key,
                create_payment=container.create_payment(),
                get_payment=container.get_payment(),
            )
            yield
        finally:
            await container.close()

    app = FastAPI(
        title="Payment processing service",
        description=(
            "Сервис асинхронной обработки платежей. Нажмите **Authorize** и укажите "
            "значение `API_KEY` для запросов к API. При создании платежа заполните "
            "обязательный заголовок `Idempotency-Key`."
        ),
        lifespan=lifespan,
        dependencies=[Depends(require_api_key)],
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["X-API-Key", "Idempotency-Key", "Content-Type"],
        )

    app.include_router(router)
    return app
