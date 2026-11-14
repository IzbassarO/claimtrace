"""Real references reported not_found on the GAN paper (arXiv 1406.2661): glued venue, ICML
papers only on arXiv, reworded titles, grey literature. Candidates mirror the live answers."""

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.core.settings import get_settings
from claimtrace.refaudit import providers
from claimtrace.refaudit.match import strip_venue, word_coverage

OK = [
    ProviderOutcome(provider="crossref", ok=True, candidates=5),
    ProviderOutcome(provider="openalex", ok=True, candidates=5),
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("title", "clean"),
    [
        (
            "Theano: new features and speed improvements. Deep Learning and Unsupervised "
            "Feature Learning NIPS 2012 Workshop",
            "Theano: new features and speed improvements",
        ),
        (
            "Better mixing via deep representations. In ICML'13",
            "Better mixing via deep representations",
        ),
        (
            "Score matching for non-normalized models. J. Machine Learning Res.",
            "Score matching for non-normalized models",
        ),
        ("Is attention all you need? In Proceedings of ACL", "Is attention all you need?"),
        ("Deep residual learning. In the 2016 Conference on CVPR.", "Deep residual learning"),
        # unchanged: no venue cue, subtitle, a too-short head, nothing to strip
        ("Peer review. A case study of three journals", None),
        ("Neural networks: In search of meaning", None),
        ("Deep learning. In ICML", None),
        ("Proceedings of the national academy of sciences at 100", None),
        (None, None),
    ],
)
def test_strip_venue(title, clean):
    assert strip_venue(title) == (clean or title)


def _ref(rid, title, year, author, raw=None):
    return Reference(
        id=rid, raw=raw or f"{author} ({year}). {title}.", title=title, year=year, authors=[author]
    )


def _work(title, year, author, provider="crossref"):
    return Work(provider=provider, title=title, year=year, authors=[author])


def test_reworded_title_same_author_and_year_is_possible_match():
    ref = _ref("b6", "Theano: a CPU and GPU math expression compiler", 2010, "Bergstra, J")
    works = [_work("Theano: A CPU and GPU Math Compiler in Python", 2010, "Bergstra, James")]
    a = refaudit.decide(ref, works, OK)
    assert a.status == RefStatus.unchecked and a.note.startswith("possible match: Theano")


def test_reworded_title_needs_most_content_words():
    ref = _ref("x", "Theano: a CPU and GPU benchmark for sparse solvers", 2010, "Bergstra, J")
    works = [_work("Theano: A CPU and GPU Math Compiler in Python", 2010, "Bergstra, James")]
    assert word_coverage(ref.title, works[0].title) < 0.8
    assert refaudit.decide(ref, works, OK).status == RefStatus.not_found


def test_same_author_prefix_title_years_apart_is_possible_match():
    # Crossref only has the 2009 book chapter; the cited 2005 JMLR paper is in neither index
    ref = _ref(
        "b17",
        "Estimation of non-normalized statistical models using score matching",
        2005,
        "Hyvärinen, A",
    )
    works = [_work("Estimation of Non-normalized Statistical Models", 2009, "Hyvärinen, Aapo")]
    a = refaudit.decide(ref, works, OK)
    assert a.status == RefStatus.unchecked and "possible match" in a.note


def test_grey_literature_is_unchecked_not_not_found():
    raw = (
        "Susskind, J., Anderson, A., and Hinton, G. E. (2010). The Toronto face dataset. "
        "Technical Report UTML TR 2010-001, U. Toronto."
    )
    ref = _ref("b27", "The Toronto face dataset", 2010, "Susskind, J", raw=raw)
    works = [_work("Trait Importance Questionnaire", 2010, "Robnett, Rachael D.")]
    a = refaudit.decide(ref, works, OK)
    assert a.status == RefStatus.unchecked and a.note == refaudit.GREY_NOTE


@pytest.mark.parametrize(
    "raw",
    ["Doe J (2020) A study of things. TR 12, MIT.", "Doe J. A study of things. PhD thesis, 2020."],
)
def test_grey_markers(raw):
    ref = _ref("x", "A study of things", 2020, "Doe, J", raw=raw)
    assert refaudit.decide(ref, [], OK).status == RefStatus.unchecked


def test_ordinary_journal_reference_stays_not_found():
    ref = _ref(
        "x",
        "A study of things",
        2020,
        "Doe, J",
        raw="Doe J (2020) A study of things. Nature 5, 1-9.",
    )
    assert refaudit.decide(ref, [], OK).status == RefStatus.not_found


# ---------- end to end ----------

FEED = '<feed xmlns="http://www.w3.org/2005/Atom">{}</feed>'
RIFAI = """<entry><id>http://arxiv.org/abs/1206.6434v1</id>
<published>2012-06-27T00:00:00Z</published>
<title>A Generative Process for Sampling Contractive Auto-Encoders</title>
<author><name>Salah Rifai</name></author><author><name>Yoshua Bengio</name></author>
<summary>x</summary></entry>"""


async def _run(ref, arxiv, openalex=None):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.host == "export.arxiv.org":
            return arxiv(req)
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": []}})
        return httpx.Response(200, json={"results": (openalex or (lambda r: []))(req)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    return a, seen


RIFAI_REF = _ref(
    "b24", "A generative process for sampling contractive auto-encoders", 2012, "Rifai, S"
)


async def test_arxiv_title_search_finds_icml_paper():
    a, seen = await _run(RIFAI_REF, lambda r: httpx.Response(200, text=FEED.format(RIFAI)))
    assert a.status == RefStatus.verified and a.matched.arxiv_id == "1206.6434"
    q = next(r for r in seen if r.url.host == "export.arxiv.org").url.params["search_query"]
    # stop words ("for") are left out: arXiv's ti: field matches nothing for them
    assert q.split(" AND ")[:4] == ["ti:generative", "ti:process", "ti:sampling", "ti:contractive"]


async def test_arxiv_title_search_failure_is_unchecked():
    a, _ = await _run(RIFAI_REF, lambda r: httpx.Response(503))
    assert a.status == RefStatus.unchecked and "arxiv" in a.note


async def test_arxiv_title_search_no_record_is_not_found():
    a, _ = await _run(RIFAI_REF, lambda r: httpx.Response(200, text=FEED.format("")))
    assert a.status == RefStatus.not_found


async def test_glued_venue_is_stripped_for_search_but_kept_in_report():
    title = "Better mixing via deep representations. In ICML'13"
    ref = _ref("b2", title, 2013, "Bengio, Y")

    def openalex(req):
        assert "ICML" not in req.url.params.get("search", "")
        return [
            {
                "id": "https://openalex.org/W1",
                "title": "Better Mixing via Deep Representations",
                "publication_year": 2012,
                "authorships": [{"author": {"display_name": "Yoshua Bengio"}}],
            }
        ]

    a, seen = await _run(ref, lambda r: httpx.Response(500), openalex)
    assert a.status == RefStatus.verified
    assert a.reference.title == title
    assert not any(r.url.host == "export.arxiv.org" for r in seen)  # identified: no fallback


async def test_arxiv_id_lookup_non_atom_page_is_a_failure():
    page = '<html xmlns="http://www.w3.org/1999/xhtml"><body>maintenance</body></html>'
    mock = httpx.MockTransport(lambda r: httpx.Response(200, text=page))
    async with httpx.AsyncClient(transport=mock) as client:
        out = await providers.arxiv_batch(client, providers.Gate(1), ["1206.6434"])
    assert isinstance(out["1206.6434"], providers.ProviderError)
