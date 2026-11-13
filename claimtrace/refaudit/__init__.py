"""Reference audit: Crossref/OpenAlex/Semantic Scholar/arXiv + deterministic matching.
Owner: T2 (docs/TASKS.md). STUB."""

from collections.abc import Callable

import httpx

from claimtrace.core.schemas import Reference, ReferenceAudit


async def audit_references(
    refs: list[Reference],
    client: httpx.AsyncClient,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ReferenceAudit]:
    raise NotImplementedError
