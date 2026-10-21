from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
from urllib.parse import urlsplit

import httpx

from application.exceptions import WebhookRejected

type IPAddress = IPv4Address | IPv6Address

LOCAL_NETWORKS = tuple(
    ip_network(network)
    for network in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
    )
)
TRANSITION_NETWORKS = tuple(
    ip_network(network) for network in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32")
)
CLOUD_SERVICE_ADDRESSES = frozenset(
    ip_address(value) for value in ("168.63.129.16", "fd00:ec2::254", "fd20:ce::254")
)


@dataclass(frozen=True)
class WebhookURLPolicy:
    allow_local: bool = False

    def validate(self, url: str) -> None:
        try:
            parsed = httpx.URL(url)
            authority = urlsplit(url).netloc
        except (httpx.InvalidURL, ValueError) as exc:
            raise WebhookRejected("Invalid webhook URL") from exc

        schemes = ("http", "https") if self.allow_local else ("https",)
        if parsed.scheme not in schemes or not parsed.host:
            raise WebhookRejected("Webhook URL must use HTTPS and have a host")
        if len(url) > 2048:
            raise WebhookRejected("Webhook URL must not exceed 2048 characters")
        if parsed.userinfo or "@" in authority or "#" in url:
            raise WebhookRejected("Webhook URL must not contain credentials or a fragment")
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535 or (not self.allow_local and port != 443):
            raise WebhookRejected("Webhook port is not allowed")
        if "%" in parsed.host:
            raise WebhookRejected("Scoped IP addresses are not allowed")
        try:
            address = ip_address(parsed.host)
        except ValueError:
            return  # Domain addresses are resolved and checked at connection time.
        self.validate_address(address)

    def validate_address(self, address: IPAddress) -> None:
        if isinstance(address, IPv6Address):
            if address.scope_id is not None or any(address in net for net in TRANSITION_NETWORKS):
                raise WebhookRejected("IPv6 scope and transition addresses are not allowed")
            address = address.ipv4_mapped or address

        if address in CLOUD_SERVICE_ADDRESSES:
            raise WebhookRejected("Cloud infrastructure endpoints are not allowed")
        if self.allow_local and any(address in network for network in LOCAL_NETWORKS):
            return
        if not address.is_global or address.is_multicast or address.is_reserved:
            raise WebhookRejected("Webhook destination is not a public unicast IP address")
