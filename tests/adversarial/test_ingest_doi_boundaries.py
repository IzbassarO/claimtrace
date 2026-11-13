"""find_doi must return the cited DOI, not the DOI plus whatever text surrounds it.

Each input below is a correct reference whose DOI is written in a common form (Wiley PDF
hyphen, Word smart quotes, a publisher link with a query string, a Markdown line break, a DOI
wrapped across lines when copied from a PDF). find_doi returns a string that is not a DOI;
Crossref, OpenAlex and doi.org all answer "no such DOI", and the real paper is reported as
not_found (DOI-only reference) or as metadata_mismatch "does not resolve (doi.org)".
"""

import asyncio
from urllib.parse import unquote

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import Reference, RefStatus
from claimtrace.core.settings import get_settings
from claimtrace.ingest.text import find_doi

HUANG = "10.1016/s0140-6736(20)30183-5"  # Huang et al., Lancet 2020


@pytest.mark.xfail(strict=True, reason="BUG: find_doi keeps U+2010 hyphen (Wiley PDFs) in DOI")
def test_unicode_hyphen_from_pdf_is_a_hyphen():
    raw = "Huang C, et al. Lancet. 2020;395:497-506. doi:10.1016/S0140‐6736(20)30183-5"
    # today: "10.1016/s0140‐6736(20)30183-5" (U+2010), which no registry knows
    assert find_doi(raw) == HUANG


@pytest.mark.xfail(strict=True, reason="BUG: find_doi keeps a closing smart quote in the DOI")
def test_smart_quotes_around_doi_link():
    raw = "Huang C, et al. Lancet. 2020. Available: “https://doi.org/10.1016/S0140-6736(20)30183-5”"
    # today: "10.1016/s0140-6736(20)30183-5”"; a straight '"' is already excluded
    assert find_doi(raw) == HUANG


@pytest.mark.xfail(strict=True, reason="BUG: find_doi keeps a publisher URL's query string")
def test_publisher_link_query_string_is_not_part_of_the_doi():
    raw = (
        "Smith A. Title. Angew Chem. 2020;59:1-5. "
        "https://onlinelibrary.wiley.com/doi/10.1002/anie.201915678?af=R"
    )
    # today: "10.1002/anie.201915678?af=r"
    assert find_doi(raw) == "10.1002/anie.201915678"


@pytest.mark.xfail(strict=True, reason="BUG: PR #67 regression, find_doi keeps a trailing <br>")
def test_markdown_line_break_tag_after_doi():
    # .md manuscripts are accepted; '<br>' is the usual hard line break there.
    # before PR #67 '<' ended the DOI; SICI support now lets the tag in
    raw = "LeCun Y, et al. Deep learning. Nature. 2015;521:436-444. doi:10.1038/nature14539<br>"
    # today: "10.1038/nature14539<br>"
    assert find_doi(raw) == "10.1038/nature14539"


@pytest.mark.xfail(strict=True, reason="BUG: DOI wrapped after '-' (copied from PDF) truncated")
def test_doi_wrapped_after_hyphen_is_joined():
    # pasted reference lists keep the PDF's hard wraps inside an entry (split_entries joins
    # wrapped lines with '\n'); a DOI never ends in '-'
    raw = "Huang C, et al. Lancet. 2020;395:497-506. doi:10.1016/S0140-\n6736(20)30183-5"
    # today: "10.1016/s0140-"
    assert find_doi(raw) == HUANG


# ---------- end to end: the mangled DOI makes a real paper not_found ----------

ITEM = {
    "DOI": "10.1016/S0140-6736(20)30183-5",
    "title": ["Clinical features of patients infected with 2019 novel coronavirus in Wuhan, China"],
    "author": [{"family": "Huang", "given": "Chaolin"}],
    "issued": {"date-parts": [[2020]]},
}


@pytest.fixture
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
        found = path.removeprefix("/api/handles/").lower() == HUANG
        return httpx.Response(200 if found else 404, json={"responseCode": 1 if found else 100})
    if req.url.host == "api.crossref.org" and path.startswith("/works/"):
        if path.removeprefix("/works/").lower() != HUANG:
            return httpx.Response(404)
        return httpx.Response(200, json={"message": ITEM})
    if path.startswith("/works/doi:"):
        return httpx.Response(404)
    if req.url.host == "api.openalex.org":
        return httpx.Response(200, json={"results": []})
    return httpx.Response(200, json={"message": {"items": []}})


@pytest.mark.xfail(strict=True, reason="BUG: real DOI with a Wiley-PDF hyphen ends as not_found")
async def test_unicode_hyphen_doi_is_not_reported_nonexistent(offline):
    raw = "doi:10.1016/S0140‐6736(20)30183-5"
    # DOI-only reference (as when the LLM extraction failed)
    ref = Reference(id="1", raw=raw, doi=find_doi(raw))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    # today: not_found, "no matching record in crossref, openalex, doi.org"
    assert a.status != RefStatus.not_found
