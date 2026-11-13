import asyncio
import json
from pathlib import Path

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.core.settings import get_settings
from claimtrace.refaudit.providers import parse_arxiv, parse_crossref, parse_openalex, parse_s2

FIX = Path(__file__).parent / "fixtures" / "refaudit"


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Deterministic settings (no S2 key, whatever .env says) and no real sleeping."""
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    get_settings.cache_clear()

    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    yield
    get_settings.cache_clear()


# ---------- parsers on saved responses ----------


def test_parse_crossref_retracted():
    w = parse_crossref(load("crossref_work_retracted.json")["message"])
    assert w.doi == "10.1016/s0140-6736(97)11096-0"
    assert w.year == 1998 and w.authors[0] == "Wakefield, AJ" and w.venue == "The Lancet"
    assert w.is_retracted


def test_parse_crossref_search_unescapes_titles():
    items = load("crossref_search.json")["message"]["items"]
    works = [parse_crossref(i) for i in items]
    assert works[0].title == "Deep learning & convolutional networks"
    assert works[1].doi == "10.1038/nature14539" and works[1].year == 2015
    assert not any(w.is_retracted for w in works)


def test_parse_openalex():
    w = parse_openalex(load("openalex_work.json"))
    assert w.title == "Why Most Published Research Findings Are False"
    assert w.doi == "10.1371/journal.pmed.0020124" and w.year == 2005
    assert w.pmid == "16060722" and w.pmcid == "PMC1182327"
    assert w.abstract.startswith("There is increasing concern")
    assert w.authors[0] == "John P. A. Ioannidis" and not w.is_retracted


def test_parse_openalex_search_arxiv_doi():
    w = parse_openalex(load("openalex_search.json")["results"][0])
    assert w.title == "Attention Is All You Need" and w.arxiv_id == "1706.03762"


def test_parse_s2():
    w = parse_s2(load("s2_paper.json"))
    assert w.pmcid == "PMC1182327" and w.pmid == "16060722" and w.year == 2005
    assert w.oa_pdf_url.startswith("https://journals.plos.org")


def test_parse_arxiv():
    works = {w.arxiv_id: w for w in parse_arxiv((FIX / "arxiv_query.xml").read_text())}
    assert set(works) == {"1706.03762", "hep-th/9711200"}
    v = works["1706.03762"]
    assert v.title == "Attention Is All You Need" and v.year == 2017
    assert v.authors[0] == "Ashish Vaswani" and len(v.authors) == 8
    assert works["hep-th/9711200"].doi == "10.1023/a:1026654312961"


# ---------- status logic (pure) ----------

REF = Reference(id="1", raw="", title="Attention Is All You Need", year=2017, authors=["Vaswani A"])
GOOD = Work(
    provider="crossref", title="Attention Is All You Need", year=2017, authors=["Vaswani, A"]
)
OK = ProviderOutcome(provider="crossref", ok=True, candidates=1)
DOWN = ProviderOutcome(provider="openalex", ok=False, error="HTTP 503")


def test_decide_verified_even_if_other_provider_down():
    a = refaudit.decide(REF, [GOOD], [OK, DOWN])
    assert a.status == RefStatus.verified and a.matched.title == GOOD.title


def test_decide_failure_without_match_is_unchecked_never_not_found():
    a = refaudit.decide(REF, [], [OK, DOWN])
    assert a.status == RefStatus.unchecked and "openalex" in a.note


def test_decide_all_answered_no_match_is_not_found():
    a = refaudit.decide(REF, [], [OK, ProviderOutcome(provider="openalex", ok=True)])
    assert a.status == RefStatus.not_found


def test_decide_no_providers_is_unchecked():
    assert refaudit.decide(REF, [], []).status == RefStatus.unchecked


def test_decide_title_exists_but_unconfirmed_is_unchecked():
    other = Work(provider="openalex", title=REF.title, year=2010, authors=["Doe, J"])
    a = refaudit.decide(REF, [other], [OK])
    assert a.status == RefStatus.unchecked and "neither year" in a.note


def test_decide_retraction_from_any_identified_record_wins():
    retracted = Work(
        provider="openalex", title=REF.title, year=2017, authors=["A. Vaswani"], is_retracted=True
    )
    a = refaudit.decide(REF.model_copy(update={"year": 2020}), [GOOD, retracted], [OK])
    assert a.status == RefStatus.retracted
    assert [m.field for m in a.mismatches] == ["year"]  # still listed


def test_decide_merges_fields_from_same_work():
    oa = Work(provider="openalex", title=REF.title, year=2017, abstract="Abs", pmcid="PMC1")
    a = refaudit.decide(REF, [oa, GOOD], [OK])
    assert a.matched.provider == "crossref"
    assert a.matched.abstract == "Abs" and a.matched.pmcid == "PMC1"


def test_decide_prefers_id_equal_and_fewest_mismatches():
    ref = REF.model_copy(update={"year": 2018, "doi": "10.1/j"})
    preprint = Work(provider="arxiv", title=REF.title, year=2016, authors=["Ashish Vaswani"])
    journal = GOOD.model_copy(update={"doi": "10.1/j", "year": 2018})
    a = refaudit.decide(ref, [preprint, journal], [OK])
    assert a.status == RefStatus.verified and a.matched.doi == "10.1/j"


# ---------- end to end with MockTransport ----------

LANCET = "10.1016/s0140-6736(97)11096-0"


def transport(routes):
    """routes: host -> handler(request) -> Response."""

    def handler(req: httpx.Request):
        return routes[req.url.host](req)

    return httpx.MockTransport(handler)


async def run(refs, routes):
    async with httpx.AsyncClient(transport=transport(routes)) as client:
        return await refaudit.audit_references(refs, client)


def crossref_ok(req):
    if req.url.path.startswith("/works/"):
        return httpx.Response(200, json=load("crossref_work_retracted.json"))
    return httpx.Response(200, json=load("crossref_search.json"))


def oa_404(req):
    return httpx.Response(404, json={"error": "not found"})


def oa_empty(req):
    if req.url.path.startswith("/works/doi:"):
        return httpx.Response(404)
    return httpx.Response(200, json={"results": []})


def down(req):
    return httpx.Response(503)


async def test_retracted_by_doi():
    ref = Reference(
        id="w",
        raw="",
        title="Ileal-lymphoid-nodular hyperplasia, non-specific "
        "colitis, and pervasive developmental disorder in children",
        year=1998,
        doi=LANCET.upper(),
    )
    [a] = await run([ref], {"api.crossref.org": crossref_ok, "api.openalex.org": oa_404})
    assert a.status == RefStatus.retracted and not a.mismatches
    assert [(p.provider, p.ok) for p in a.providers] == [("crossref", True), ("openalex", True)]


async def test_provider_503_gives_unchecked_never_not_found():
    ref = Reference(id="x", raw="", title="A fabricated paper about nothing at all", year=2020)
    empty_cr = {"api.crossref.org": lambda r: httpx.Response(200, json={"message": {"items": []}})}
    [a] = await run([ref], empty_cr | {"api.openalex.org": down})
    assert a.status == RefStatus.unchecked
    oa = next(p for p in a.providers if p.provider == "openalex")
    assert not oa.ok and "503" in oa.error


@pytest.mark.parametrize("code", [401, 403])
async def test_openalex_auth_failure_is_unchecked(code):
    ref = Reference(id="x", raw="", title="A fabricated paper about nothing at all", year=2020)
    empty_cr = {"api.crossref.org": lambda r: httpx.Response(200, json={"message": {"items": []}})}
    [a] = await run([ref], empty_cr | {"api.openalex.org": lambda r: httpx.Response(code)})
    assert a.status == RefStatus.unchecked
    assert [(p.provider, p.ok) for p in a.providers] == [("crossref", True), ("openalex", False)]


async def test_transport_error_and_garbage_json_are_failures():
    def boom(req):
        raise httpx.ConnectError("nope")

    ref = Reference(id="x", raw="", title="A fabricated paper about nothing at all", year=2020)
    garbage = {"api.crossref.org": lambda r: httpx.Response(200, text="<html>")}
    [a] = await run([ref], garbage | {"api.openalex.org": boom})
    assert a.status == RefStatus.unchecked
    assert not any(p.ok for p in a.providers)


async def test_fabricated_reference_all_answered_is_not_found():
    ref = Reference(
        id="x",
        raw="",
        title="A fabricated paper about nothing at all",
        year=2020,
        doi="10.9999/fake",
    )
    empty_cr = {
        "api.crossref.org": lambda r: (
            httpx.Response(404)
            if r.url.path.startswith("/works/")
            else httpx.Response(200, json={"message": {"items": []}})
        )
    }
    [a] = await run([ref], empty_cr | {"api.openalex.org": oa_empty})
    assert a.status == RefStatus.not_found
    assert all(p.ok for p in a.providers)


async def test_wrong_doi_falls_back_to_title_search():
    ref = Reference(
        id="d",
        raw="",
        title="Deep learning",
        year=2015,
        authors=["LeCun Y"],
        doi="10.1038/nature99999",
    )
    routes = {
        "api.crossref.org": lambda r: (
            httpx.Response(404) if r.url.path.startswith("/works/") else crossref_ok(r)
        ),
        "api.openalex.org": oa_empty,
    }
    [a] = await run([ref], routes)
    assert a.status == RefStatus.metadata_mismatch
    assert [m.field for m in a.mismatches] == ["doi"]
    assert a.matched.doi == "10.1038/nature14539"


async def test_arxiv_batched_and_semantic_scholar_with_key(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "k")
    get_settings.cache_clear()
    seen = {"arxiv": 0, "s2_key": None}

    def arxiv(req):
        seen["arxiv"] += 1
        assert set(req.url.params["id_list"].split(",")) == {"1706.03762", "hep-th/9711200"}
        return httpx.Response(200, text=(FIX / "arxiv_query.xml").read_text())

    def s2(req):
        seen["s2_key"] = req.headers.get("x-api-key")
        return httpx.Response(404)

    refs = [
        Reference(id="v", raw="", title="Attention is all you need", arxiv_id="1706.03762v2"),
        Reference(
            id="m",
            raw="",
            title="The large N limit of superconformal field theories and supergravity",
            year=1997,
            arxiv_id="hep-th/9711200",
        ),
    ]
    routes = {
        "api.crossref.org": lambda r: httpx.Response(200, json={"message": {"items": []}}),
        "api.openalex.org": oa_empty,
        "export.arxiv.org": arxiv,
        "api.semanticscholar.org": s2,
    }
    progress = []
    async with httpx.AsyncClient(transport=transport(routes)) as client:
        out = await refaudit.audit_references(refs, client, lambda d, t: progress.append((d, t)))
    assert [a.status for a in out] == [RefStatus.verified, RefStatus.verified]
    assert out[0].matched.provider == "arxiv"
    assert seen == {"arxiv": 1, "s2_key": "k"}
    assert {p.provider for p in out[0].providers} == {
        "crossref",
        "openalex",
        "semanticscholar",
        "arxiv",
    }
    assert sorted(progress) == [(1, 2), (2, 2)]


async def test_arxiv_failure_makes_arxiv_only_ref_unchecked():
    ref = Reference(id="v", raw="", arxiv_id="1706.03762")
    routes = {
        "api.crossref.org": lambda r: httpx.Response(200, json={"message": {"items": []}}),
        "api.openalex.org": oa_empty,
        "export.arxiv.org": down,
    }
    [a] = await run([ref], routes)
    assert a.status == RefStatus.unchecked


async def test_nothing_to_look_up_is_unchecked_without_requests():
    [a] = await run([Reference(id="n", raw="Some garbage")], {})
    assert a.status == RefStatus.unchecked and not a.providers
