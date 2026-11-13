"""A provider outage must not end as a negative-looking 'source_not_found'."""

import httpx
import pytest

import claimtrace.sources as src
from claimtrace.claims import check_claim
from claimtrace.core.schemas import Citation, Reference, ReferenceAudit, RefStatus, Verdict, Work
from claimtrace.core.settings import Settings


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("claimtrace.core.http.asyncio.sleep", no_sleep)
    settings = Settings(_env_file=None, contact_email="test@example.org")
    monkeypatch.setattr(src, "get_settings", lambda: settings)


@pytest.mark.xfail(
    strict=True, reason="BUG: every provider 503 -> fetch_source None -> source_not_found"
)
async def test_all_providers_down_is_insufficient_evidence_not_source_not_found():
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    work = Work(
        provider="crossref",
        title="Vitamin D supplementation and fracture risk in older adults",
        doi="10.1000/vitd.2020",
        oa_pdf_url="https://repo.example.org/vitd.pdf",
    )
    audit = ReferenceAudit(
        reference=Reference(id="1", raw="x"), status=RefStatus.verified, matched=work
    )
    source = await src.fetch_source(audit, down, None)
    cite = Citation(id="c1", sentence="Vitamin D cut hip fractures by half [1].", ref_ids=["1"])
    check = await check_claim(cite, "1", source)
    # Same pair as pipeline._sources -> _claims. Outage must read as "could not check".
    assert check.verdict == Verdict.insufficient_evidence
