"""SSRF guard for endpoints that fetch a user-supplied base_url server-side.

Blocks loopback, private, link-local and reserved address ranges so an operator
(or anyone who reaches the dashboard) cannot use the server as a proxy to probe
internal services or cloud metadata endpoints (169.254.169.254).

Note: this is a best-effort defence. It resolves the hostname and checks every
returned IP. A determined attacker with DNS-rebinding can still race the check,
but it stops the common cases.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")


class UrlNotAllowed(ValueError):
    """Raised when a URL fails the SSRF policy."""


def validate_outbound_url(
    url: str, *, allow_private: bool = False, allow_loopback: bool = False
) -> str:
    """Return the URL if it is safe to fetch server-side, else raise UrlNotAllowed.

    allow_loopback=True permits only loopback targets for a deliberately local
    router. allow_private=True additionally permits private network targets.
    Link-local / reserved / multicast / unspecified addresses are always
    blocked, so cloud-metadata SSRF (169.254.169.254) stays blocked.
    """
    if not url or not isinstance(url, str):
        raise UrlNotAllowed("URL trống hoặc không hợp lệ")
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlNotAllowed(f"Chỉ cho phép http/https, nhận '{parsed.scheme}'")
    host = parsed.hostname
    if not host:
        raise UrlNotAllowed("URL thiếu host")

    # Resolve hostname to all IPs and reject disallowed targets.
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UrlNotAllowed(f"Không phân giải được host '{host}'")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        # Always block these — never legitimate even for a local router.
        if (ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise UrlNotAllowed(f"Host '{host}' trỏ tới địa chỉ bị chặn ({ip_str})")
        if ip.is_loopback and not (allow_loopback or allow_private):
            raise UrlNotAllowed(f"Host '{host}' trỏ tới địa chỉ nội bộ ({ip_str})")
        if ip.is_private and not ip.is_loopback and not allow_private:
            raise UrlNotAllowed(f"Host '{host}' trỏ tới địa chỉ nội bộ ({ip_str})")
    return url.strip()
