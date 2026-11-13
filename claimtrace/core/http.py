"""One shared httpx client factory + a retrying request helper."""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx

from claimtrace.core.settings import get_settings

RETRY_STATUS = {429, 500, 502, 503, 504}
OPENALEX_HOST = "api.openalex.org"
_KEY_RE = re.compile(r"(api_key=)[^&\s\"']+")

# httpx logs full request URLs at INFO; those can carry the OpenAlex key.
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)


def redact(text: str) -> str:
    """Hide API keys carried in URLs (safe for logs, errors, reports)."""
    return _KEY_RE.sub(r"\1***", text)


async def _add_openalex_key(request: httpx.Request) -> None:
    key = get_settings().openalex_api_key.get_secret_value()
    if key and request.url.host == OPENALEX_HOST and "api_key" not in request.url.params:
        request.url = request.url.copy_add_param("api_key", key)


_run_searches = 0  # paid OpenAlex requests made by this process


def _spend_search(root: Path) -> bool:
    """Count one paid OpenAlex request against the per-run and the daily (UTC) budget.
    False = budget exhausted, or the counter can't be persisted (fail closed: it costs money)."""
    global _run_searches
    s = get_settings()
    if s.openalex_run_searches and _run_searches >= s.openalex_run_searches:
        return False
    # ponytail: plain counter file, racy across processes; one app process + eval is fine
    day = root / f"searches-{datetime.now(UTC):%Y-%m-%d}.txt"
    try:
        used = int(day.read_text() or 0) if day.exists() else 0
        if used >= s.openalex_daily_searches:
            return False
        root.mkdir(parents=True, exist_ok=True)
        day.write_text(str(used + 1))
    except (OSError, ValueError) as e:
        log.warning("OpenAlex search counter unavailable (%s); refusing paid request", e)
        return False
    _run_searches += 1
    return True


class OpenAlexTransport(httpx.AsyncBaseTransport):
    """Disk cache + search budget for api.openalex.org GETs. List/search requests (`/works?...`)
    cost money (daily budget, resets 00:00 UTC); entity lookups (`/works/doi:...`) are free.
    Over budget => HTTP 402, which providers treat as a failure (unchecked, never not_found)."""

    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != OPENALEX_HOST or request.method != "GET":
            return await self.inner.handle_async_request(request)
        url = str(request.url.copy_remove_param("api_key"))
        root = Path(get_settings().cache_dir) / "openalex"
        f = root / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        try:
            hit = json.loads(f.read_text())
            return httpx.Response(
                hit["status"],
                content=hit["body"].encode(),
                headers={"content-type": "application/json", "x-claimtrace-cache": "hit"},
                request=request,
            )
        except (OSError, ValueError, KeyError):
            pass
        if request.url.path.count("/") <= 1 and not _spend_search(root):
            return httpx.Response(
                402, json={"error": "ClaimTrace OpenAlex search budget exhausted"}, request=request
            )
        resp = await self.inner.handle_async_request(request)
        if resp.status_code in (200, 404):  # answers, not failures: safe to reuse
            await resp.aread()
            try:
                root.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps({"status": resp.status_code, "body": resp.text}))
                tmp.replace(f)
            except OSError as e:  # a cache problem never breaks a lookup
                log.warning("OpenAlex cache write failed: %s", e)
        return resp


def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    email = get_settings().contact_email
    ua = "ClaimTrace/0.1 (citation audit" + (f"; mailto:{email})" if email else ")")
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": ua},
        limits=httpx.Limits(max_connections=20),
        transport=OpenAlexTransport(
            httpx.AsyncHTTPTransport(limits=httpx.Limits(max_connections=20))
        ),
        event_hooks={"request": [_add_openalex_key]},
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
