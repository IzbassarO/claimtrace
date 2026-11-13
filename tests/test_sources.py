from pathlib import Path

import httpx
import pytest

import claimtrace.sources as src
from claimtrace.core.schemas import Reference, ReferenceAudit, RefStatus, Work
from claimtrace.core.settings import Settings

JATS = (Path(__file__).parent / "fixtures/sources/article.jats.xml").read_bytes()
TITLE = "Vitamin D supplementation and fracture risk in older adults"
DOI = "10.1000/vitd.2020"
LONG_JATS = JATS.replace(
    b"</body>", b"<sec><p>" + b"More results text. " * 150 + b"</p></sec></body>"
)
PDF = b"%PDF-1.7 fake"
GOOD_TEXT = TITLE + "\n\n" + "Supplementation reduced hip fractures in older women. " * 60
OTHER_TEXT = "Deep residual learning for image recognition. " + "Convolutional networks. " * 120


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("claimtrace.core.http.asyncio.sleep", no_sleep)
    settings = Settings(_env_file=None, contact_email="test@example.org")
    monkeypatch.setattr(src, "get_settings", lambda: settings)


def audit(**work) -> ReferenceAudit:
    w = Work(provider=work.pop("provider", "crossref"), title=TITLE, **work)
    return ReferenceAudit(
        reference=Reference(id="1", raw="x"), status=RefStatus.verified, matched=w
    )


def client(routes: dict, seen: list | None = None) -> httpx.AsyncClient:
    """routes: "host/path" -> Response | Exception | callable(request). Unknown => 404."""

    def handler(req: httpx.Request) -> httpx.Response:
        key = req.url.host + req.url.path
        if seen is not None:
            seen.append(key)
        hit = routes.get(key)
        if hit is None:
            return httpx.Response(404)
        if isinstance(hit, Exception):
            raise hit
        return hit(req) if callable(hit) else hit

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def pdf_resp(body=PDF, ctype="application/pdf", **headers) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": ctype, **headers})


def fake_pdf_to_text(text=GOOD_TEXT, calls: list | None = None):
    async def f(pdf: bytes) -> str:
        assert pdf.startswith(b"%PDF")
        if calls is not None:
            calls.append(pdf)
        if isinstance(text, Exception):
            raise text
        return text

    return f


EPMC_FULL = "www.ebi.ac.uk/europepmc/webservices/rest/PMC1234567/fullTextXML"
EPMC_SEARCH = "www.ebi.ac.uk/europepmc/webservices/rest/search"
ARXIV_PDF = "arxiv.org/pdf/2101.00001"


# ---------- JATS ----------


def test_jats_keeps_title_body_captions_tables_and_drops_references():
    text = src.jats_to_text(JATS)
    assert text.startswith(TITLE)
    assert "reduced hip fractures by 18% (p<0.05) in women aged 70 to 80." in text
    assert "Fractures are common in older adults [1]." in text
    assert "Kaplan-Meier curves by treatment arm." in text
    assert "Placebo | 1021" in text and "Baseline characteristics." in text
    assert "totally different reference" not in text
    assert "thank the participants" not in text
    assert "Journal of Examples" not in text


# ---------- guard ----------


def test_guard_needs_length_and_title_words():
    assert src.looks_like(GOOD_TEXT, TITLE)
    assert not src.looks_like(GOOD_TEXT[:1500], TITLE)  # too short
    assert not src.looks_like(OTHER_TEXT, TITLE)  # wrong paper
    assert not src.looks_like(GOOD_TEXT, None)
    late = "filler words here. " * 400 + TITLE  # title only after the first 5000 chars
    assert not src.looks_like(late, TITLE)
    assert src.looks_like(
        GOOD_TEXT.upper().replace("VITAMIN", "Vitámin"), "Vitamin D supplementation"
    )


# ---------- full text ----------


async def test_europepmc_by_pmcid_without_prefix():
    async with client({EPMC_FULL: httpx.Response(200, content=LONG_JATS)}) as c:
        s = await src.fetch_source(audit(pmcid="1234567"), c, fake_pdf_to_text())
    assert s.kind == "full_text" and s.origin == "europepmc"


async def test_short_full_text_is_rejected():
    async with client({EPMC_FULL: httpx.Response(200, content=JATS)}) as c:
        s = await src.fetch_source(audit(pmcid="PMC1234567"), c, fake_pdf_to_text())
    assert s is None  # fixture is < 2000 chars and there is no abstract


async def test_europepmc_by_doi_search():
    seen: list = []
    routes = {
        EPMC_SEARCH: lambda r: httpx.Response(
            200, json={"resultList": {"result": [{"id": "1", "pmcid": "PMC1234567"}]}}
        ),
        EPMC_FULL: httpx.Response(200, content=LONG_JATS),
    }
    async with client(routes, seen) as c:
        s = await src.fetch_source(audit(doi=DOI), c, fake_pdf_to_text())
    assert s.kind == "full_text" and s.origin == "europepmc"
    assert s.url == "https://europepmc.org/article/PMC/PMC1234567"
    assert "reduced hip fractures by 18%" in s.text
    assert seen[:2] == [EPMC_SEARCH, EPMC_FULL]


async def test_europepmc_down_falls_through_to_arxiv_pdf():
    calls: list = []
    routes = {
        EPMC_FULL: httpx.Response(503),
        ARXIV_PDF: pdf_resp(),
    }
    async with client(routes) as c:
        s = await src.fetch_source(
            audit(pmcid="1234567", arxiv_id="2101.00001"), c, fake_pdf_to_text(calls=calls)
        )
    assert s.kind == "full_text" and s.origin == "arxiv"
    assert s.url == "https://arxiv.org/pdf/2101.00001" and s.text == GOOD_TEXT.strip()
    assert len(calls) == 1


async def test_arxiv_id_from_datacite_doi():
    async with client({ARXIV_PDF: pdf_resp()}) as c:
        s = await src.fetch_source(audit(doi="10.48550/arXiv.2101.00001"), c, fake_pdf_to_text())
    assert s.origin == "arxiv"


async def test_wrong_document_falls_back_to_abstract():
    async with client({ARXIV_PDF: pdf_resp()}) as c:
        s = await src.fetch_source(
            audit(arxiv_id="2101.00001", abstract="We tested vitamin D in 4,000 adults."),
            c,
            fake_pdf_to_text(OTHER_TEXT),
        )
    assert s.kind == "abstract" and s.origin == "crossref"
    assert s.text == "We tested vitamin D in 4,000 adults."
    assert s.url == "https://arxiv.org/abs/2101.00001"


async def test_pdf_to_text_failure_tries_next_source():
    routes = {
        ARXIV_PDF: pdf_resp(),
        "api.unpaywall.org/v2/" + DOI: httpx.Response(
            200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.org/p.pdf"}}
        ),
        "oa.example.org/p.pdf": pdf_resp(),
    }
    texts = iter([RuntimeError("GROBID unavailable"), GOOD_TEXT])

    async def flaky(pdf: bytes) -> str:
        t = next(texts)
        if isinstance(t, Exception):
            raise t
        return t

    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=DOI, arxiv_id="2101.00001"), c, flaky)
    assert s.origin == "unpaywall" and s.url == "https://oa.example.org/p.pdf"


async def test_unpaywall_rejects_landing_page_then_uses_matched_oa_url():
    routes = {
        "api.unpaywall.org/v2/" + DOI: httpx.Response(
            200, json={"best_oa_location": {"url_for_pdf": "https://pub.example.org/landing"}}
        ),
        "pub.example.org/landing": pdf_resp(b"<html>buy</html>", "text/html"),
        "oa.example.org/p.pdf": pdf_resp(),
    }
    async with client(routes) as c:
        s = await src.fetch_source(
            audit(doi=DOI, oa_pdf_url="https://oa.example.org/p.pdf"), c, fake_pdf_to_text()
        )
    assert s.origin == "unpaywall" and s.url == "https://oa.example.org/p.pdf"


async def test_pdf_size_and_magic_limits():
    big = pdf_resp(**{"content-length": str(31 * 1024 * 1024)})
    async with client({"a.org/big.pdf": big, "a.org/fake.pdf": pdf_resp(b"<html>")}) as c:
        assert await src._get_pdf(c, "https://a.org/big.pdf") is None
        assert await src._get_pdf(c, "https://a.org/fake.pdf") is None


# ---------- abstracts ----------


async def test_abstract_from_openalex_inverted_index():
    inv = {"Vitamin": [0], "D": [1], "reduced": [2], "fractures.": [3]}
    routes = {
        "api.openalex.org/works/doi:" + DOI: httpx.Response(
            200, json={"abstract_inverted_index": inv}
        )
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=DOI), c, fake_pdf_to_text())
    assert s.kind == "abstract" and s.origin == "openalex"
    assert s.text == "Vitamin D reduced fractures." and s.url == "https://doi.org/" + DOI


async def test_abstract_providers_down_then_crossref_jats_stripped():
    jats = (
        "<jats:title>Abstract</jats:title><jats:p>Vitamin D reduced fractures "
        "by 18&#x00025; in <jats:italic>women</jats:italic>.</jats:p>"
    )
    routes = {
        "api.openalex.org/works/doi:" + DOI: httpx.Response(503),
        "api.semanticscholar.org/graph/v1/paper/DOI:" + DOI: httpx.ConnectError("down"),
        "api.crossref.org/works/" + DOI: httpx.Response(200, json={"message": {"abstract": jats}}),
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=DOI), c, fake_pdf_to_text())
    assert s.origin == "crossref"
    assert s.text == "Vitamin D reduced fractures by 18% in women."


async def test_semantic_scholar_abstract():
    routes = {
        "api.semanticscholar.org/graph/v1/paper/DOI:" + DOI: httpx.Response(
            200, json={"abstract": "Vitamin D reduced fractures."}
        )
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=DOI, provider="openalex"), c, fake_pdf_to_text())
    assert s.origin == "semanticscholar" and s.text == "Vitamin D reduced fractures."


async def test_everything_down_returns_none_without_raising():
    async with client({}) as c:
        assert await src.fetch_source(audit(doi=DOI), c, fake_pdf_to_text()) is None

    def boom(req):
        raise httpx.ConnectError("no network")

    c = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    async with c:
        s = await src.fetch_source(
            audit(doi=DOI, pmcid="PMC1", arxiv_id="2101.00001", oa_pdf_url="https://x.org/a.pdf"),
            c,
            fake_pdf_to_text(),
        )
    assert s is None


async def test_no_matched_work_returns_none():
    a = ReferenceAudit(reference=Reference(id="1", raw="x"), status=RefStatus.not_found)
    async with client({}) as c:
        assert await src.fetch_source(a, c, fake_pdf_to_text()) is None
