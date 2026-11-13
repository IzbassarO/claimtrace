"""OpenAlex budget: search is billed, DOI lookups are free. Which URLs get requested, and
budget/rate answers (402/429/403) are provider failures, never not_found."""

import asyncio

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import Reference, RefStatus
from claimtrace.core.settings import get_settings
from claimtrace.refaudit.providers import _jats, parse_arxiv, parse_openalex, parse_s2

CR_EMPTY = {"message": {"items": []}}
LECUN = {
    "DOI": "10.1038/nature14539",
    "title": ["Deep learning"],
    "author": [{"family": "LeCun", "given": "Yann"}],
    "issued": {"date-parts": [[2015, 5]]},
}
OA_LECUN = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.1038/nature14539",
    "title": "Deep learning",
    "publication_year": 2015,
    "authorships": [{"author": {"display_name": "Yann LeCun"}}],
    "ids": {"pmcid": "https://www.ncbi.nlm.nih.gov/pmc/articles/123"},
    "abstract_inverted_index": {"Deep": [0], "learning": [1]},
}
REF = Reference(id="1", raw="", title="Deep learning", year=2015, authors=["LeCun Y"])


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


async def run(ref, crossref, openalex):
    seen = []

    def handler(req: httpx.Request):
        if req.url.host == "api.openalex.org":
            seen.append(req.url.path + ("?search" if "search" in req.url.params else ""))
            return openalex(req)
        return crossref(req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    return a, seen


def cr_search(req):
    if req.url.path.startswith("/works/"):
        return httpx.Response(404)
    return httpx.Response(200, json={"message": {"items": [LECUN]}})


def oa_by_doi(req):
    if req.url.path == "/works/doi:10.1038/nature14539":
        return httpx.Response(200, json=OA_LECUN)
    if req.url.path.startswith("/works/doi:"):
        return httpx.Response(404)
    raise AssertionError("OpenAlex search must not be called")


async def test_doi_known_uses_only_free_lookups():
    ref = REF.model_copy(update={"doi": "10.1038/NATURE14539"})

    def cr(req):
        assert req.url.path == "/works/10.1038/nature14539"
        return httpx.Response(200, json={"message": LECUN})

    a, seen = await run(ref, cr, oa_by_doi)
    assert a.status == RefStatus.verified and a.matched.pmcid == "PMC123"
    assert seen == ["/works/doi:10.1038/nature14539"]


async def test_crossref_identified_then_openalex_by_its_doi_not_search():
    a, seen = await run(REF, cr_search, oa_by_doi)
    assert a.status == RefStatus.verified and a.matched.provider == "crossref"
    assert a.matched.abstract == "Deep learning" and a.matched.pmcid == "PMC123"
    assert seen == ["/works/doi:10.1038/nature14539"]


async def test_wrong_doi_crossref_identified_by_search_no_openalex_search():
    ref = REF.model_copy(update={"doi": "10.1038/nature99999"})
    a, seen = await run(ref, cr_search, oa_by_doi)
    assert a.status == RefStatus.metadata_mismatch and [m.field for m in a.mismatches] == ["doi"]
    assert seen == ["/works/doi:10.1038/nature99999", "/works/doi:10.1038/nature14539"]


@pytest.mark.parametrize(
    "crossref",
    [lambda r: httpx.Response(200, json=CR_EMPTY), lambda r: httpx.Response(503)],
    ids=["crossref-no-match", "crossref-down"],
)
async def test_openalex_search_only_when_crossref_did_not_identify(crossref):
    def oa(req):
        return httpx.Response(200, json={"results": [OA_LECUN]})

    a, seen = await run(REF, crossref, oa)
    assert a.status == RefStatus.verified and a.matched.provider == "openalex"
    assert seen == ["/works?search"]


async def test_crossref_hit_missing_on_openalex_is_still_verified():
    a, seen = await run(REF, cr_search, lambda r: httpx.Response(404))
    assert a.status == RefStatus.verified
    assert seen == ["/works/doi:10.1038/nature14539"]


BUDGET = [
    httpx.Response(402, json={"error": "search budget exhausted"}),
    httpx.Response(
        429, json={"error": "Rate limit exceeded", "message": "daily budget of $1 exceeded"}
    ),
    httpx.Response(403, json={"error": "Insufficient credits"}),
]


@pytest.mark.parametrize("resp", BUDGET, ids=lambda r: str(r.status_code))
async def test_openalex_budget_answer_is_unchecked_never_not_found(resp):
    ref = Reference(id="x", raw="", title="A fabricated paper about nothing at all", year=2020)
    a, seen = await run(ref, lambda r: httpx.Response(200, json=CR_EMPTY), lambda r: resp)
    assert a.status == RefStatus.unchecked
    oa = next(p for p in a.providers if p.provider == "openalex")
    assert not oa.ok and str(resp.status_code) in oa.error
    assert set(seen) == {"/works?search"}


async def test_budget_exhausted_but_crossref_identified_is_verified():
    a, _ = await run(REF, cr_search, lambda r: httpx.Response(402))
    assert a.status == RefStatus.verified


# ---------- abstracts: only Crossref JATS loses tags ----------

PLAIN = "Doses < 10 mg or > 50 mg; a<b and c>d at 10<sup>5</sup> cells."


def test_plain_text_abstracts_keep_angle_brackets():
    assert parse_s2({"abstract": PLAIN}).abstract == PLAIN
    inv = {w: [i] for i, w in enumerate(PLAIN.split())}
    assert parse_openalex({"abstract_inverted_index": inv}).abstract == PLAIN
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2101.00001v1'
        "</id><summary>Doses &lt; 10 mg or\n  &gt; 50 mg</summary></entry></feed>"
    )
    assert parse_arxiv(xml)[0].abstract == "Doses < 10 mg or > 50 mg"


def test_crossref_jats_abstract_maps_sup_sub_and_strips_only_real_tags():
    jats = (
        "<jats:title>Abstract</jats:title><jats:p>We used 10<jats:sup>5</jats:sup> cells, "
        "x<sub>i</sub> &lt; 3 and doses &gt; 50 mg in <jats:italic>E. coli</jats:italic>."
        "</jats:p>"
    )
    assert _jats(jats) == "Abstract We used 10^5 cells, x_i < 3 and doses > 50 mg in E. coli."


async def test_arxiv_id_uses_free_datacite_doi_lookup():
    ref = Reference(
        id="v", raw="", title="Attention is all you need", year=2017, arxiv_id="1706.03762v5"
    )
    oa_work = {
        "id": "https://openalex.org/W2",
        "doi": "https://doi.org/10.48550/arxiv.1706.03762",
        "title": "Attention Is All You Need",
        "publication_year": 2017,
    }

    def oa(req):
        assert req.url.path == "/works/doi:10.48550/arxiv.1706.03762"
        return httpx.Response(200, json=oa_work)

    def handler(req):
        if req.url.host == "api.openalex.org":
            return oa(req)
        if req.url.host == "export.arxiv.org":
            return httpx.Response(503)
        return httpx.Response(200, json=CR_EMPTY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    assert a.status == RefStatus.verified and a.matched.provider == "openalex"
