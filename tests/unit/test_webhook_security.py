import asyncio
import ssl
from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from application.exceptions import WebhookError, WebhookRejected
from infrastructures.webhooks.http import HTTPWebhookSender
from infrastructures.webhooks.policy import WebhookURLPolicy
from infrastructures.webhooks.transport import SafeWebhookTransport

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V6 = "2606:4700:4700::1111"


async def test_real_http_transport_preserves_tls_hostname_and_connects_to_checked_ip(monkeypatch):
    connections = []
    streams = []

    class Stream(httpcore.AsyncNetworkStream):
        def __init__(self):
            self.written = b""
            self.tls_name = None
            self.closed = False

        async def read(self, max_bytes, timeout=None):  # noqa: ASYNC109 -- httpcore interface
            return b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"

        async def write(self, buffer, timeout=None):  # noqa: ASYNC109 -- httpcore interface
            self.written += buffer

        async def aclose(self):
            self.closed = True

        async def start_tls(
            self, ssl_context, server_hostname=None, timeout=None  # noqa: ASYNC109 -- httpcore
        ):
            assert ssl_context.verify_mode == ssl.CERT_REQUIRED
            assert ssl_context.check_hostname
            self.tls_name = server_hostname
            return self

        def get_extra_info(self, info):
            return None

    async def connect_tcp(backend, host, port, **kwargs):
        connections.append((host, port))
        stream = Stream()
        streams.append(stream)
        return stream

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connect_tcp)
    transport = SafeWebhookTransport(
        WebhookURLPolicy(), resolver=AsyncMock(return_value=[PUBLIC_V4])
    )
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        for hostname in ("merchant.example", "another.example"):
            response = await client.post(f"https://{hostname}/hook", json={"event": "paid"})
            assert response.text == "ok"

    assert connections == [(PUBLIC_V4, 443), (PUBLIC_V4, 443)]
    assert [stream.tls_name for stream in streams] == ["merchant.example", "another.example"]
    assert all(stream.closed for stream in streams)
    assert b"Host: merchant.example\r\n" in streams[0].written
    assert b"Host: another.example\r\n" in streams[1].written


@pytest.mark.parametrize(
    "url",
    [
        "http://merchant.example/hook",
        "ftp://merchant.example/hook",
        "/hook",
        "https://user:pass@merchant.example/hook",
        "https://merchant.example/hook#part",
        "https://merchant.example:8443/hook",
        "https://merchant.example:0/hook",
        "https://127.0.0.1/hook",
        "https://10.0.0.1/hook",
        "https://192.168.1.1/hook",
        "https://169.254.169.254/hook",
        "https://100.100.100.200/hook",
        "https://0.0.0.0/",
        "https://224.0.0.1/",
        "https://240.0.0.1/",
        "https://[::1]/",
        "https://[::]/",
        "https://[fe80::1]/",
        "https://[fd00::1]/",
        "https://[ff02::1]/",
        "https://[::ffff:127.0.0.1]/",
        "https://[::ffff:169.254.169.254]/",
        "https://[64:ff9b::7f00:1]/",
        "https://[2002:7f00:1::]/",
        "https://[fe80::1%25eth0]/",
        "https://168.63.129.16/",
    ],
)
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(WebhookRejected):
        WebhookURLPolicy().validate(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://merchant.example/hook",
        f"https://{PUBLIC_V4}/hook",
        f"https://[{PUBLIC_V6}]/hook",
    ],
)
def test_public_https_urls_are_allowed(url):
    WebhookURLPolicy().validate(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://api:9009/hook",
        "http://127.0.0.1:9000/hook",
        "http://[::1]:9000/hook",
        "http://192.168.1.2:9000/hook",
        "http://[fd00::2]:9000/hook",
    ],
)
def test_development_mode_explicitly_allows_local_receivers(url):
    WebhookURLPolicy(allow_local=True).validate(url)


@pytest.mark.parametrize(
    "host",
    [
        "169.254.169.254",
        "168.63.129.16",
        "[fd00:ec2::254]",
        "[fd20:ce::254]",
        "0.0.0.0",
        "224.0.0.1",
        "[::]",
        "[ff02::1]",
        "[fe80::1]",
    ],
)
def test_development_mode_still_blocks_infrastructure_and_special_addresses(host):
    with pytest.raises(WebhookRejected):
        WebhookURLPolicy(allow_local=True).validate(f"http://{host}/hook")


@pytest.mark.parametrize(
    "answers",
    [
        ["127.0.0.1"],
        ["::1"],
        [PUBLIC_V4, "10.0.0.1"],
        [PUBLIC_V4, "::ffff:169.254.169.254"],
        [PUBLIC_V6, "fe80::1"],
        ["not-an-ip"],
    ],
)
async def test_all_dns_answers_are_checked_before_any_connection(payment, answers):
    factory = AsyncMock()  # Must never be called, not even for the public answer.
    transport = SafeWebhookTransport(
        WebhookURLPolicy(), resolver=AsyncMock(return_value=answers), transport_factory=factory
    )
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        with pytest.raises(WebhookRejected):
            await HTTPWebhookSender(client).send(payment)
    factory.assert_not_called()


async def test_dns_rebinding_is_checked_on_each_attempt_and_hostname_is_pinned(payment):
    resolver = AsyncMock(side_effect=[[PUBLIC_V4], ["127.0.0.1"]])
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(204)

    transport = SafeWebhookTransport(
        WebhookURLPolicy(),
        resolver=resolver,
        transport_factory=lambda: httpx.MockTransport(receive),
    )
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        sender = HTTPWebhookSender(client)
        await sender.send(payment)
        with pytest.raises(WebhookRejected):
            await sender.send(payment)

    assert resolver.await_count == 2
    assert len(requests) == 1
    assert requests[0].url.host == PUBLIC_V4
    assert requests[0].headers["Host"] == "merchant.example"
    assert requests[0].extensions["sni_hostname"] == "merchant.example"


async def test_fallback_uses_only_validated_addresses_and_closes_transports(payment):
    requests = []
    transports = []

    def receive(request):
        requests.append(request)
        if request.url.host == PUBLIC_V6:
            raise httpx.ConnectError("IPv6 unavailable", request=request)
        return httpx.Response(204)

    class TrackedTransport(httpx.MockTransport):
        closed = False

        async def aclose(self):
            self.closed = True

    def factory():
        inner = TrackedTransport(receive)
        transports.append(inner)
        return inner

    transport = SafeWebhookTransport(
        WebhookURLPolicy(),
        resolver=AsyncMock(return_value=[PUBLIC_V6, PUBLIC_V4]),
        transport_factory=factory,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await HTTPWebhookSender(client).send(payment)
    assert [r.url.host for r in requests] == [PUBLIC_V6, PUBLIC_V4]
    assert all(t.closed for t in transports)


async def test_read_failure_does_not_replay_post_on_another_address(payment):
    requests = []

    def receive(request):
        requests.append(request)
        raise httpx.ReadTimeout("Response lost", request=request)

    transport = SafeWebhookTransport(
        WebhookURLPolicy(),
        resolver=AsyncMock(return_value=[PUBLIC_V4, PUBLIC_V6]),
        transport_factory=lambda: httpx.MockTransport(receive),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WebhookError):
            await HTTPWebhookSender(client).send(payment)
    assert len(requests) == 1


async def test_redirect_is_not_followed_even_if_client_enables_redirects(payment):
    requests = []

    def receive(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    transport = SafeWebhookTransport(
        WebhookURLPolicy(),
        resolver=AsyncMock(return_value=[PUBLIC_V4]),
        transport_factory=lambda: httpx.MockTransport(receive),
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(WebhookError):
            await HTTPWebhookSender(client).send(payment)
    assert len(requests) == 1


async def test_dns_timeout_fails_closed_without_connecting(payment):
    async def stalled(host, port):
        await asyncio.Event().wait()

    factory = AsyncMock()
    transport = SafeWebhookTransport(
        WebhookURLPolicy(), resolver=stalled, dns_timeout=0.01, transport_factory=factory
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(WebhookError):
            await HTTPWebhookSender(client).send(payment)
    factory.assert_not_called()


async def test_untrusted_response_body_is_not_read(payment):
    class NeverRead(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            raise AssertionError("Webhook response body must not be buffered")
            yield  # pragma: no cover

        async def aclose(self):
            self.closed = True

    stream = NeverRead()
    transport = SafeWebhookTransport(
        WebhookURLPolicy(),
        resolver=AsyncMock(return_value=[PUBLIC_V4]),
        transport_factory=lambda: httpx.MockTransport(
            lambda request: httpx.Response(204, stream=stream)
        ),
    )
    async with httpx.AsyncClient(transport=transport) as client:
        await HTTPWebhookSender(client).send(payment)
    assert stream.closed
