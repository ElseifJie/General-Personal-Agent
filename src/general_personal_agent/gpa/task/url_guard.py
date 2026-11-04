"""Validation for outbound HTTP URLs supplied by tasks or remote agents.

The A2A executor sends a bearer token and reads back artifacts, so every URL it
follows is a potential SSRF sink. Only http(s) URLs are accepted, the host must
be explicitly trusted (``A2A_ALLOWED_HOSTS``) or resolve exclusively to globally
routable addresses, and loopback/private/link-local (cloud metadata)/reserved
addresses are rejected.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from urllib.parse import urlsplit

Resolver = Callable[[str], Awaitable[list[str]]]


class OutboundUrlError(ValueError):
    """An outbound URL is not safe to call."""


async def _default_resolver(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _routable(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """False for loopback/private/link-local/reserved/multicast addresses."""
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return bool(mapped.is_global)
    return bool(ip.is_global)


def _host_allowed(host: str, allowed_hosts: Sequence[str]) -> bool:
    return any(host == item or host.endswith(f".{item}") for item in allowed_hosts)


async def validate_outbound_url(
    url: str,
    allowed_hosts: Sequence[str] = (),
    *,
    resolver: Resolver | None = None,
) -> str:
    """Return *url* if it is safe for an outbound HTTP call, else raise.

    ``allowed_hosts`` is the recommended production control: when non-empty the
    host must match an entry (exact or subdomain) and DNS is skipped because the
    operator has explicitly trusted it. Without an allowlist, hostnames are
    resolved and every resolved address must be globally routable; a resolution
    failure fails closed. IP literals are always checked against that rule.

    The URL is validated immediately before use. Operators should still set an
    allowlist: a hostname that passes resolution here could be rebound before
    the HTTP client connects.
    """
    if not isinstance(url, str) or not url:
        raise OutboundUrlError("outbound URL is empty")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise OutboundUrlError("outbound URL must use http or https")
    if parts.username or parts.password:
        raise OutboundUrlError("outbound URL must not embed credentials")
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise OutboundUrlError("outbound URL has no host")
    if allowed_hosts and not _host_allowed(host, allowed_hosts):
        raise OutboundUrlError(f"host {host!r} is not in A2A_ALLOWED_HOSTS")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _routable(literal):
            raise OutboundUrlError(f"host {host!r} is not a public address")
    elif not allowed_hosts:
        resolve = resolver or _default_resolver
        try:
            addresses = await resolve(host)
        except OSError as exc:
            raise OutboundUrlError(f"cannot resolve host {host!r}") from exc
        if not addresses:
            raise OutboundUrlError(f"host {host!r} resolved to no address")
        for address in addresses:
            if not _routable(ipaddress.ip_address(address)):
                raise OutboundUrlError(f"host {host!r} resolves to a non-public address")
    return url
