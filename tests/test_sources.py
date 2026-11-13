import os
import time
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
def _offline(monkeypatch, tmp_path):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("claimtrace.core.http.asyncio.sleep", no_sleep)
    settings = Settings(_env_file=None, contact_email="test@example.org", cache_dir=str(tmp_path))
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
    assert text.startswith(TITLE + "\n\nGarcía Okafor\n\nWe tested")
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
    assert src.looks_like(GOOD_TEXT.upper().replace("VITAMIN", "Vitámin"), TITLE)


def test_guard_short_title_needs_first_author_family_name():
    text = "Deep learning\n\nYann LeCun, Yoshua Bengio\n\n" + "Representation learning. " * 100
    assert src.looks_like(text, "Deep learning", ["LeCun, Yann", "Bengio, Yoshua"])
    assert src.looks_like(text, "Deep learning", ["Yann LeCun"])
    assert not src.looks_like(text, "Deep learning", ["Hinton, Geoffrey"])
    assert not src.looks_like(text, "Deep learning", [])  # cannot check: fall back to abstract
    long_title = "Deep learning about representation"  # 3 content words: no author check
    assert src.looks_like(text.replace("Deep learning", long_title, 1), long_title)


ASPIRIN = "Aspirin reduces mortality in older adults"
BODY = "Participants were followed for five years in the trial. " * 50


def test_guard_rejects_opposite_title_on_first_line():
    assert src.looks_like(ASPIRIN + "\n\n" + BODY, ASPIRIN)
    assert src.looks_like("Aspirin Reduces Mortality in Older Adults.\n\n" + BODY, ASPIRIN)
    for wrong in (
        "Aspirin does not reduce mortality in older adults",
        "Aspirin doesn't reduce mortality in older adults",
        "Aspirin fails to reduce mortality in older adults",
        "Aspirin reduces mortality in older adults aged 70",  # extra number
        "Statins reduce mortality in older adults with diabetes",  # different paper
    ):
        assert not src.looks_like(wrong + "\n\n" + BODY, ASPIRIN), wrong


def test_guard_without_title_line_checks_first_sentence_negation():
    # No newline: the first "line" is a 2000+ char blob, so the overlap rule applies, plus
    # the negation words of the first sentence must equal the cited title's.
    blob = "Aspirin does not reduce mortality in older adults. " + BODY
    assert not src.looks_like(blob, ASPIRIN)
    assert src.looks_like("Aspirin reduces mortality in older adults. " + BODY, ASPIRIN)


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


async def test_unpaywall_wrong_document_pdf_falls_through_to_matched_oa_url():
    routes = {
        "api.unpaywall.org/v2/" + DOI: httpx.Response(
            200, json={"best_oa_location": {"url_for_pdf": "https://pub.example.org/a.pdf"}}
        ),
        "pub.example.org/a.pdf": pdf_resp(b"%PDF-1.7 other"),
        "oa.example.org/p.pdf": pdf_resp(),
    }

    async def by_pdf(pdf: bytes) -> str:
        return OTHER_TEXT if pdf.endswith(b"other") else GOOD_TEXT

    async with client(routes) as c:
        s = await src.fetch_source(
            audit(doi=DOI, oa_pdf_url="https://oa.example.org/p.pdf"), c, by_pdf
        )
    assert s.kind == "full_text" and s.origin == "unpaywall"
    assert s.url == "https://oa.example.org/p.pdf" and s.text == GOOD_TEXT.strip()


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
        "by 18&#x00025; (p<0.05) in <jats:italic>women</jats:italic>.</jats:p>"
    )
    routes = {
        "api.openalex.org/works/doi:" + DOI: httpx.Response(503),
        "api.semanticscholar.org/graph/v1/paper/DOI:" + DOI: httpx.ConnectError("down"),
        "api.crossref.org/works/" + DOI: httpx.Response(200, json={"message": {"abstract": jats}}),
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=DOI), c, fake_pdf_to_text())
    assert s.origin == "crossref"
    assert s.text == "Vitamin D reduced fractures by 18% (p<0.05) in women."


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


# ---------- bioRxiv / medRxiv, OpenAlex locations ----------

BIO_DOI = "10.1101/2020.03.22.002386"
OA_WORK = "api.openalex.org/works/doi:" + DOI


def biorxiv_details(versions):
    return httpx.Response(200, json={"collection": [{"version": str(v)} for v in versions]})


async def test_biorxiv_latest_version_pdf():
    seen: list = []
    routes = {
        "api.biorxiv.org/details/biorxiv/" + BIO_DOI: biorxiv_details([1, 3, 2]),
        f"www.biorxiv.org/content/{BIO_DOI}v3.full.pdf": pdf_resp(),
    }
    async with client(routes, seen) as c:
        s = await src.fetch_source(audit(doi=BIO_DOI), c, fake_pdf_to_text())
    assert s.kind == "full_text" and s.origin == "biorxiv"
    assert s.url == f"https://www.biorxiv.org/content/{BIO_DOI}v3.full.pdf"
    assert "api.unpaywall.org/v2/" + BIO_DOI not in seen  # bioRxiv comes before Unpaywall


async def test_medrxiv_when_biorxiv_has_no_record():
    med = "10.1101/2020.04.14.20062463"
    routes = {
        "api.biorxiv.org/details/biorxiv/" + med: biorxiv_details([]),
        "api.biorxiv.org/details/medrxiv/" + med: biorxiv_details([2]),
        f"www.medrxiv.org/content/{med}v2.full.pdf": pdf_resp(),
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=med), c, fake_pdf_to_text())
    assert s.origin == "biorxiv" and s.url == f"https://www.medrxiv.org/content/{med}v2.full.pdf"


async def test_cshl_journal_doi_is_not_sent_to_biorxiv():
    seen: list = []
    async with client({}, seen) as c:
        await src.fetch_source(audit(doi="10.1101/gr.123456.111"), c, fake_pdf_to_text())
    assert not any(k.startswith("api.biorxiv.org") for k in seen)


async def test_biorxiv_blocked_falls_through_to_unpaywall():
    routes = {
        "api.biorxiv.org/details/biorxiv/" + BIO_DOI: biorxiv_details([1]),
        f"www.biorxiv.org/content/{BIO_DOI}v1.full.pdf": pdf_resp(b"<html>", "text/html"),
        "api.unpaywall.org/v2/" + BIO_DOI: httpx.Response(
            200, json={"best_oa_location": {"url_for_pdf": "https://oa.example.org/p.pdf"}}
        ),
        "oa.example.org/p.pdf": pdf_resp(),
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=BIO_DOI), c, fake_pdf_to_text())
    assert s.origin == "unpaywall" and s.url == "https://oa.example.org/p.pdf"


async def test_openalex_locations_each_pass_the_guard():
    work = {
        "best_oa_location": {"pdf_url": "https://oa.example.org/p.pdf"},  # tried by Unpaywall
        "primary_location": {"pdf_url": None},
        "locations": [
            {"pdf_url": "https://repo.example.org/other.pdf"},  # another paper
            {"pdf_url": None},
            {"pdf_url": "https://repo.example.org/right.pdf"},
        ],
    }
    routes = {
        OA_WORK: httpx.Response(200, json=work),
        "oa.example.org/p.pdf": pdf_resp(b"%PDF-1.7 broken"),
        "repo.example.org/other.pdf": pdf_resp(b"%PDF-1.7 other"),
        "repo.example.org/right.pdf": pdf_resp(),
    }
    calls: list = []

    async def by_pdf(pdf: bytes) -> str:
        calls.append(pdf)
        if pdf.endswith(b"broken"):
            raise RuntimeError("GROBID could not parse")
        return OTHER_TEXT if pdf.endswith(b"other") else GOOD_TEXT

    async with client(routes) as c:
        s = await src.fetch_source(
            audit(doi=DOI, oa_pdf_url="https://oa.example.org/p.pdf"), c, by_pdf
        )
    assert s.kind == "full_text" and s.origin == "openalex"
    assert s.url == "https://repo.example.org/right.pdf"
    assert len(calls) == 3  # the url Unpaywall already tried is not downloaded twice


async def test_openalex_locations_all_wrong_falls_back_to_openalex_abstract():
    work = {
        "locations": [{"pdf_url": "https://repo.example.org/other.pdf"}],
        "abstract_inverted_index": {"Vitamin": [0], "D.": [1]},
    }
    routes = {
        OA_WORK: httpx.Response(200, json=work),
        "repo.example.org/other.pdf": pdf_resp(),
    }
    async with client(routes) as c:
        s = await src.fetch_source(audit(doi=DOI), c, fake_pdf_to_text(OTHER_TEXT))
    assert s.kind == "abstract" and s.origin == "openalex" and s.text == "Vitamin D."


# ---------- disk cache ----------


def cache_files(tmp_path) -> list[Path]:
    return sorted((tmp_path / "sources").glob("*.json"))


async def test_cache_hit_skips_network_and_takes_new_ref_id(tmp_path):
    seen: list = []
    async with client({ARXIV_PDF: pdf_resp()}, seen) as c:
        first = await src.fetch_source(audit(arxiv_id="2101.00001"), c, fake_pdf_to_text())
    assert first.origin == "arxiv" and len(cache_files(tmp_path)) == 1
    seen.clear()
    again = audit(arxiv_id="2101.00001")
    again.reference.id = "7"
    async with client({}, seen) as c:
        s = await src.fetch_source(again, c, fake_pdf_to_text(RuntimeError("not called")))
    assert seen == [] and s.ref_id == "7" and s.text == first.text and s.url == first.url


async def test_none_and_failures_are_not_cached(tmp_path):
    async with client({ARXIV_PDF: httpx.Response(503)}) as c:
        assert await src.fetch_source(audit(arxiv_id="2101.00001"), c, fake_pdf_to_text()) is None
    assert cache_files(tmp_path) == []


async def test_corrupt_cache_file_is_ignored_and_replaced(tmp_path):
    a = audit(doi=DOI, abstract="We tested vitamin D in 4,000 adults.")
    f = src._cache_file(a.matched)
    f.parent.mkdir(parents=True)
    f.write_text('{"ref_id": "1", "kind": "full_text", "te')
    async with client({}) as c:
        s = await src.fetch_source(a, c, fake_pdf_to_text())
    assert s.kind == "abstract" and src.SourceText.model_validate_json(f.read_text()) == s


async def test_stale_abstract_is_refetched_but_full_text_is_kept(tmp_path):
    old = time.time() - src.ABSTRACT_TTL - 60
    a = audit(doi=DOI, abstract="Old abstract.")
    async with client({}) as c:
        await src.fetch_source(a, c, fake_pdf_to_text())
    f = src._cache_file(a.matched)
    os.utime(f, (old, old))
    a.matched.abstract = "New abstract."
    async with client({}) as c:
        assert (await src.fetch_source(a, c, fake_pdf_to_text())).text == "New abstract."
    full = src.SourceText(ref_id="1", kind="full_text", origin="arxiv", text=GOOD_TEXT)
    f.write_text(full.model_dump_json())
    os.utime(f, (old, old))
    async with client({}) as c:
        assert (await src.fetch_source(a, c, fake_pdf_to_text())).kind == "full_text"


async def test_cache_keeps_only_newest_files(tmp_path, monkeypatch):
    monkeypatch.setattr(src, "CACHE_MAX_FILES", 2)
    async with client({}) as c:
        for i in range(4):
            a = audit(doi=f"10.1000/x{i}", abstract=f"Abstract {i}.")
            await src.fetch_source(a, c, fake_pdf_to_text())
            os.utime(src._cache_file(a.matched), (1000 + i, 1000 + i))
    kept = {f.name for f in cache_files(tmp_path)}
    assert kept == {src._cache_file(audit(doi=f"10.1000/x{i}").matched).name for i in (2, 3)}


def test_cache_key_title_needs_year_and_first_author():
    a = Work(provider="crossref", title=TITLE, year=2020, authors=["Smith, J"])
    b = Work(provider="crossref", title=TITLE.upper(), year=2020, authors=["J Smith"])
    assert src._cache_file(a) == src._cache_file(b)
    assert src._cache_file(a) != src._cache_file(a.model_copy(update={"year": 2021}))
    assert src._cache_file(a) != src._cache_file(a.model_copy(update={"authors": ["Lee, K"]}))
    assert src._cache_file(Work(provider="crossref")) is None


# ---------- arXiv HTML ----------

ARXIV_HTML = "arxiv.org/html/2101.00001"
HTML_PAGE = (Path(__file__).parent / "fixtures/sources/arxiv.html").read_text()
LONG_HTML = HTML_PAGE.replace("BODY", "Supplementation reduced hip fractures. " * 80)


def html_resp(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


def test_arxiv_html_to_text():
    text = src.arxiv_html_to_text(HTML_PAGE)
    lines = text.split("\n\n")
    assert lines[0] == TITLE  # title first, even after a licence note
    assert lines[1] == "Licence note printed above the title."
    assert lines[2] == "Jane Smith Ahmed Khan"  # no footnote marks, notes or emails
    assert "We gave 4,000 adults vitamin D & calcium." in lines
    assert "1 Introduction" in lines
    assert "Hip fractures fell by 10^5 per h_{t}, as reported [1]." in text  # LaTeX, not "ht"
    assert "Figure 1: Fracture rates." in lines
    for gone in (
        "Unrelated cited paper",
        "References",
        "localStorage",
        "color: red",
        "toclist",
        "1 Introduction\n\n1",
        "Equal contribution",
        "jane@",
        "LaTeXML",
    ):
        assert gone not in text, gone


async def test_arxiv_html_before_pdf():
    calls: list = []
    async with client({ARXIV_HTML: html_resp(LONG_HTML), ARXIV_PDF: pdf_resp()}) as c:
        s = await src.fetch_source(audit(arxiv_id="2101.00001"), c, fake_pdf_to_text(calls=calls))
    assert s.kind == "full_text" and s.origin == "arxiv"
    assert s.url == "https://arxiv.org/html/2101.00001" and s.text.startswith(TITLE + "\n\n")
    assert calls == []  # no PDF download / conversion needed


async def test_arxiv_html_wrong_paper_or_error_falls_through_to_pdf():
    wrong = LONG_HTML.replace("Vitamin D supplementation", "Deep residual learning")
    for page in (html_resp(wrong), httpx.Response(200, text="<p>x</p>"), httpx.Response(503)):
        async with client({ARXIV_HTML: page, ARXIV_PDF: pdf_resp()}) as c:
            s = await src.fetch_source(audit(arxiv_id="2101.00001"), c, fake_pdf_to_text())
        assert s.origin == "arxiv" and s.url == "https://arxiv.org/pdf/2101.00001"
