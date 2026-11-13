"""One shared httpx client factory + a retrying request helper."""

import asyncio

import httpx

from claimtrace.core.settings import get_settings

RETRY_STATUS = {429, 500, 502, 503, 504}


def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    email = get_settings().contact_email
    ua = "ClaimTrace/0.1 (citation audit" + (f"; mailto:{email})" if email else ")")
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": ua},
        limits=httpx.Limits(max_connections=20),
    )


async def request(
    client: httpx.AsyncClient, method: str, url: str, *, retries: int = 2, **kw
) -> httpx.Response:
    """Retry 429/5xx and transport errors. Returns the last response; raises the last
    httpx.TransportError. Callers decide what a status means (404 on a DOI lookup is an
    answer, 503 is a provider failure)."""
    for attempt in range(retries + 1):
        try:
            resp = await client.request(method, url, **kw)
        except httpx.TransportError:
            if attempt == retries:
                raise
            delay = 2.0**attempt
        else:
            if resp.status_code not in RETRY_STATUS or attempt == retries:
                return resp
            try:
                delay = float(resp.headers.get("retry-after", ""))
            except ValueError:
                delay = 2.0**attempt
        await asyncio.sleep(min(delay, 10.0))
    raise AssertionError("unreachable")
