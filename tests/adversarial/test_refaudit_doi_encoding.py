"""A real DOI written in an encoded / PDF-mangled form must never come back as not_found.

The manuscript cites the right DOI, but as a URL-encoded link or with an invisible zero-width /
soft-hyphen character (PDF line breaking). The DOI string is sent to Crossref, OpenAlex and
doi.org as is (re-encoded), every service answers "no such DOI", and the paper is reported
as nonexistent: a fabricated-reference finding for a real paper.
"""

import asyncio
from urllib.parse import unquote

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import Reference, RefStatus
from claimtrace.core.settings import get_settings
from claimtrace.ingest.text import find_doi

REAL = "10.1016/s0140-6736(20)30183-5"  # Huang et al., Lancet 2020
ITEM = {
    "DOI": "10.1016/S0140-6736(20)30183-5",
    "title": ["Clinical features of patients infected with 2019 novel coronavirus in Wuhan, China"],
    "author": [{"family": "Huang", "given": "Chaolin"}],
    "issued": {"date-parts": [[2020]]},
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    get_settings.cache_clear()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    yield
    get_settings.cache_clear()


def handler(req: httpx.Request) -> httpx.Response:
    """Real services decode the path once and compare DOIs case-insensitively."""
    path = unquote(req.url.raw_path.decode().split("?")[0])
    if req.url.host == "doi.org":
        found = path.removeprefix("/api/handles/").lower() == REAL
        return httpx.Response(200 if found else 404, json={"responseCode": 1 if found else 100})
    if req.url.host == "api.crossref.org" and path.startswith("/works/"):
        if path.removeprefix("/works/").lower() != REAL:
            return httpx.Response(404)
        return httpx.Response(200, json={"message": ITEM})
    if path.startswith("/works/doi:"):
        return httpx.Response(404)
    if req.url.host == "api.openalex.org":
        return httpx.Response(200, json={"results": []})
    return httpx.Response(200, json={"message": {"items": []}})


@pytest.mark.parametrize(
    "raw",
    [
        "https://doi.org/10.1016/S0140-6736%2820%2930183-5",  # URL-encoded link
        "doi:10.1016/S0140-6736​(20)30183-5",  # zero-width space from a PDF/web copy
        "doi:10.1016/S0140-­6736(20)30183-5",  # soft hyphen from PDF line breaking
    ],
    ids=["url-encoded", "zero-width-space", "soft-hyphen"],
)
async def test_real_doi_in_encoded_form_is_not_not_found(raw):
    # DOI-only reference (as when the LLM extraction failed: only DOI/arXiv/year are used)
    ref = Reference(id="1", raw=raw, doi=find_doi(raw))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    # today: not_found, "no matching record in crossref, openalex, doi.org"
    assert a.status != RefStatus.not_found
