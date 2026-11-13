"""Rules from the second review: unresolved cited DOI, title as a whole raw field."""

import asyncio

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.match import _in_raw
from claimtrace.refaudit.providers import Gate, ProviderError, doi_registered

T = "Attention is all you need"
AX = Work(
    provider="openalex",
    title=T,
    authors=["Ashish Vaswani"],
    year=2017,
    doi="10.48550/arxiv.1706.03762",
)
REF = Reference(id="1", raw="", title=T, authors=["Vaswani, A."], year=2017, doi="10.1145/9.9")


def test_unresolved_doi_after_failed_lookup_is_no_mismatch_and_not_verified():
    outcomes = [
        ProviderOutcome(provider="crossref", ok=False, error="HTTP 503"),
        ProviderOutcome(provider="openalex", ok=True, candidates=1),
    ]
    a = refaudit.decide(REF, [AX], outcomes)
    assert a.status == RefStatus.unchecked and not a.mismatches
    assert "cited DOI not confirmed; provider failed: crossref" in a.note


ANSWERED = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]


def test_unresolved_doi_after_answered_lookups_is_a_doi_mismatch():
    outcomes = [*ANSWERED, ProviderOutcome(provider="doi.org", ok=True, candidates=0)]
    a = refaudit.decide(REF, [AX], outcomes)
    assert a.status == RefStatus.metadata_mismatch
    assert [(m.field, m.found) for m in a.mismatches] == [("doi", "does not resolve (doi.org)")]


def test_doi_registered_at_doi_org_is_not_compared():
    outcomes = [*ANSWERED, ProviderOutcome(provider="doi.org", ok=True, candidates=1)]
    a = refaudit.decide(REF, [AX], outcomes)
    assert a.status == RefStatus.verified and not a.mismatches
    assert "DOI registered (not in Crossref/OpenAlex), not compared" in a.note


@pytest.mark.parametrize("extra", [[], [ProviderOutcome(provider="doi.org", ok=False)]])
def test_doi_org_unknown_or_failed_is_no_mismatch_and_not_verified(extra):
    a = refaudit.decide(REF, [AX], ANSWERED + extra)
    assert a.status == RefStatus.unchecked and not a.mismatches


async def test_doi_registered_reads_the_handle_api_response_code(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    codes = {"10.1/yes": (200, 1), "10.1/no": (404, 100)}

    def handler(req):
        doi = req.url.path.removeprefix("/api/handles/")
        assert req.url.host == "doi.org"
        if doi not in codes:
            return httpx.Response(503)
        status, code = codes[doi]
        return httpx.Response(status, json={"responseCode": code, "handle": doi})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gate = Gate(1)
        assert await doi_registered(client, gate, "10.1/yes") is True
        assert await doi_registered(client, gate, "10.1/no") is False
        with pytest.raises(ProviderError):  # a failure is never "does not resolve"
            await doi_registered(client, gate, "10.1/down")


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ('J. Smith, "Aspirin reduces mortality," Lancet, 2019.', True),
        ("Smith J (2019) Aspirin reduces mortality. Lancet 1:2", True),
        # after a bare year / before "[": not provably the whole title field -> not compared
        ("Smith J. 2019 Aspirin reduces mortality [Internet].", False),
        ("Smith J. Low-dose aspirin reduces mortality. Lancet. 2019.", False),
        ("Smith J. Aspirin reduces mortality in the elderly. Lancet. 2019.", False),
        ("Smith J. No. No free lunch for aspirin. Lancet. 2019.", False),
    ],
)
def test_title_must_be_a_whole_field_of_the_raw(raw, ok):
    title = "No free lunch for aspirin" if "lunch" in raw else "Aspirin reduces mortality"
    assert _in_raw(raw, title) is ok
