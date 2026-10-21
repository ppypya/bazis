import secrets
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from pydantic import SecretStr

from application.use_cases.create_payment import CreatePayment
from application.use_cases.get_payment import GetPayment

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class APIDependencies:
    api_key: SecretStr
    create_payment: CreatePayment
    get_payment: GetPayment


def _get_dependencies(request: Request) -> APIDependencies:
    # Starlette state is dynamic; config.api sets this object during startup.
    return cast(APIDependencies, request.app.state.dependencies)


async def require_api_key(
    request: Request,
    key: Annotated[str | None, Depends(api_key_header)],
) -> None:
    expected = _get_dependencies(request).api_key.get_secret_value()
    if key is None or not secrets.compare_digest(key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def provide_create_payment(request: Request) -> CreatePayment:
    return _get_dependencies(request).create_payment


def provide_get_payment(request: Request) -> GetPayment:
    return _get_dependencies(request).get_payment
