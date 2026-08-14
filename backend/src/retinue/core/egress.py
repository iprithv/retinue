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
