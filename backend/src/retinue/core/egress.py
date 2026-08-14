"""Central SSRF egress guard (§16): one policy for the image proxy today and
web tools / OpenAPI actions later. DNS-resolve first, deny private / loopback /
link-local / metadata ranges, cap redirects and response size.

Residual risk (documented): the validated fetch re-resolves DNS, so a
fast-flux rebind between check and connect is theoretically possible; the
allowlist story for actions (§9.4) narrows this further at T3.
"""

import asyncio
import ipaddress
from urllib.parse import urljoin, urlsplit

import httpx

from retinue.core.errors import EGRESS_DENIED, AppError

ALLOWED_PORTS = {80, 443}
MAX_REDIRECTS = 3


def _deny(message: str) -> AppError:
    return AppError(EGRESS_DENIED, message, status=400)


# Cloud metadata endpoints — never a legitimate agent target, blocked for
# everyone including admins (§9.4 / §16 SSRF posture).
_METADATA_HOSTS = {"169.254.169.254", "metadata.google.internal", "100.100.100.100"}


def _classify_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return "blocked"  # link-local covers 169.254/16 (cloud metadata)
    if ip.is_loopback:
        return "loopback"
    if ip.is_private:
        return "private"
    return "public"


_ORDER = {"blocked": 4, "unresolved": 3, "loopback": 2, "private": 1, "public": 0}


async def classify_host(host: str) -> str:
    """Classify a host as 'public' | 'private' | 'loopback' | 'blocked'
    (link-local/metadata) | 'unresolved'. A literal IP is classified directly;
    a hostname resolving to several addresses takes its most restrictive one.
    Resolution failure is reported as 'unresolved', not an error, so callers
    can apply a trust-based policy."""
    if host.lower() in _METADATA_HOSTS:
        return "blocked"
    try:
        return _classify_ip(ipaddress.ip_address(host))  # literal IP
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None)
    except OSError:
        return "unresolved"
    if not infos:
        return "unresolved"
    worst = "public"
    for info in infos:
        klass = _classify_ip(ipaddress.ip_address(info[4][0]))
        if _ORDER[klass] > _ORDER[worst]:
            worst = klass
    return worst


async def assert_host_reachable(host: str, *, is_admin: bool) -> None:
    """Egress policy for user-configured targets (MCP HTTP url, OpenAPI /
    connector host_allowlist entries): public always; private/loopback and
    unresolvable hostnames only for admins (§9.4 'admin-expandable' — admins
    can already run host commands, so pointing at internal networks adds no
    privilege); link-local/metadata never, for anyone."""
    if not host:
        raise _deny("host is required")
    klass = await classify_host(host)
    if klass == "public":
        return
    if klass == "blocked":
        raise _deny(f"host {host!r} is a link-local/metadata address, which is never allowed")
    if not is_admin:
        detail = (
            "does not resolve to a public address"
            if klass == "unresolved"
            else f"resolves to a {klass} address"
        )
        raise _deny(f"host {host!r} {detail}; only an admin may point tools at internal networks")


async def _assert_public_host(host: str, port: int) -> None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port)
    except OSError as exc:
        raise _deny(f"cannot resolve host {host!r}") from exc
    if not infos:
        raise _deny(f"cannot resolve host {host!r}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise _deny("host resolves to a non-public address")


async def fetch_guarded(
    url: str,
    *,
    max_bytes: int = 5 * 1024 * 1024,
    timeout_s: float = 10.0,
) -> tuple[bytes, str]:
    """GET a public URL under the egress policy. Returns (body, content_type)."""
    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout_s) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if parts.scheme not in ("http", "https"):
                raise _deny("only http(s) URLs are allowed")
            if not parts.hostname:
                raise _deny("URL has no host")
            port = parts.port or (443 if parts.scheme == "https" else 80)
            if port not in ALLOWED_PORTS:
                raise _deny(f"port {port} is not allowed")
            await _assert_public_host(parts.hostname, port)

            async with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise _deny("redirect without location")
                    current = urljoin(current, location)
                    continue
                if response.status_code >= 400:
                    raise _deny(f"upstream returned {response.status_code}")
                content_type = response.headers.get("content-type", "application/octet-stream")
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > max_bytes:
                        raise _deny("response exceeds size cap")
                    chunks.append(chunk)
                return b"".join(chunks), content_type
    raise _deny("too many redirects")
