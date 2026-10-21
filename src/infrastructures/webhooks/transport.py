import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from ipaddress import ip_address

import httpx

from application.exceptions import WebhookRejected
from infrastructures.webhooks.policy import IPAddress, WebhookURLPolicy

type Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def resolve_addresses(host: str, port: int) -> list[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(str(record[4][0]) for record in records))


def create_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(trust_env=False, retries=0)


class ClosingStream(httpx.AsyncByteStream):
    def __init__(self, response: httpx.Response, transport: httpx.AsyncBaseTransport) -> None:
        self.response = response
        self.transport = transport

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self.response.aiter_raw():
            yield chunk

    async def aclose(self) -> None:
        try:
            await self.response.aclose()
        finally:
            await self.transport.aclose()


class SafeWebhookTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        policy: WebhookURLPolicy,
        *,
        resolver: Resolver = resolve_addresses,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] = create_transport,
        dns_timeout: float = 5,
    ) -> None:
        self.policy = policy
        self.resolver = resolver
        self.transport_factory = transport_factory
        self.dns_timeout = dns_timeout

    async def _addresses(self, request: httpx.Request) -> list[IPAddress]:
        self.policy.validate(str(request.url))
        host = request.url.raw_host.decode("ascii")
        try:
            return [ip_address(host)]
        except ValueError:
            pass

        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            async with asyncio.timeout(self.dns_timeout):
                resolved = await self.resolver(host, port)
        except TimeoutError as exc:
            raise httpx.ConnectTimeout("Webhook DNS lookup timed out", request=request) from exc
        except OSError as exc:
            raise httpx.ConnectError("Webhook DNS lookup failed", request=request) from exc

        if not resolved:
            raise httpx.ConnectError("Webhook host has no addresses", request=request)
        addresses = []
        for value in dict.fromkeys(resolved):
            try:
                address = ip_address(value)
            except ValueError as exc:
                raise WebhookRejected("DNS returned an invalid IP address") from exc
            self.policy.validate_address(address)
            addresses.append(address)
        return addresses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Validate EVERY answer before connecting, including mixed public/private DNS.
        addresses = await self._addresses(request)
        for index, address in enumerate(addresses):
            headers = request.headers.copy()
            headers["Host"] = request.url.netloc.decode("ascii")
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=str(address)),
                headers=headers,
                stream=request.stream,
                extensions={
                    **request.extensions,
                    "sni_hostname": request.url.raw_host.decode("ascii"),
                },
            )
            # A fresh transport prevents TLS connections being shared by distinct
            # hostnames that happen to resolve to the same IP address.
            transport = self.transport_factory()
            try:
                response = await transport.handle_async_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                await transport.aclose()
                if index == len(addresses) - 1:
                    raise
                continue  # Only connect failures; never replay a sent POST here.
            except BaseException:
                await transport.aclose()
                raise
            return httpx.Response(
                response.status_code,
                headers=response.headers,
                stream=ClosingStream(response, transport),
                extensions=response.extensions,
            )
        raise httpx.ConnectError("Webhook host has no usable addresses", request=request)
