"""Source text: Europe PMC / arXiv / Unpaywall full text, else abstract. Owner: T3. STUB."""

from collections.abc import Awaitable, Callable

import httpx

from claimtrace.core.schemas import ReferenceAudit, SourceText


async def fetch_source(
    audit: ReferenceAudit,
    client: httpx.AsyncClient,
    pdf_to_text: Callable[[bytes], Awaitable[str]],
) -> SourceText | None:
    raise NotImplementedError
