"""Adversarial (#115): the glued-venue strip, the arXiv title-search fallback, and the widened
"possible match". Offline: decide() directly, or audit_references over httpx.MockTransport."""

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.core.settings import get_settings
from claimtrace.refaudit.match import strip_venue

OK = [
    ProviderOutcome(provider="crossref", ok=True, candidates=5),
    ProviderOutcome(provider="openalex", ok=True, candidates=5),
]
FEED = '<feed xmlns="http://www.w3.org/2005/Atom">{}</feed>'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    get_settings.cache_clear()

    async def no_sleep(_):
        return None

    # core.http retries 429/5xx/timeouts with real sleeps; the gates space arXiv calls by 3 s
    monkeypatch.setattr("claimtrace.core.http.asyncio.sleep", no_sleep)
    monkeypatch.setattr("claimtrace.refaudit.providers.asyncio.sleep", no_sleep)
    yield
    get_settings.cache_clear()


def _ref(title, year, author):
    return Reference(
        id="b1", raw=f"{author} ({year}). {title}.", title=title, year=year, authors=[author]
    )


def _crossref_item(title, year, family, given, doi):
    return {
        "DOI": doi,
        "title": [title],
        "author": [{"family": family, "given": given}],
        "issued": {"date-parts": [[year]]},
    }


async def _run(ref, crossref_items=(), arxiv=None):
    def handler(req: httpx.Request):
        if req.url.host == "export.arxiv.org":
            return arxiv(req) if arxiv else httpx.Response(200, text=FEED.format(""))
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": list(crossref_items)}})
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        [a] = await refaudit.audit_references([ref], client)
    return a


# ---------- strip_venue cuts real titles at an abbreviation ----------

# The cited title is exactly the record's title; the "venue" cue is a word inside the title
# ("Workshop", "Conference") after an abbreviation's full stop ("U.S.", "E.", "St.").
ABBREV_TITLES = [
    "Recommendations from the U.S. Food and Drug Administration Workshop on Pediatric Dosing",
    "An Outbreak of E. coli O157 Infections Among Attendees of a Medical Conference",
    "Highlights of the St. Gallen International Consensus Conference on Early Breast Cancer",
]


@pytest.mark.parametrize("title", ABBREV_TITLES)
def test_strip_venue_keeps_titles_with_abbreviations(title):
    # today: 'Recommendations from the U.S', 'An Outbreak of E', 'Highlights of the St'
    assert strip_venue(title) == title


@pytest.mark.parametrize("title", ABBREV_TITLES)
async def test_exact_title_with_abbreviation_is_verified(title):
    # Crossref has the very record cited (same title, first author, year). Today the audit
    # matches on the cut title, reports a title mismatch "cited: <cut title>" and shows a
    # correct reference as metadata_mismatch.
    ref = _ref(title, 2019, "Smith, John")
    item = _crossref_item(title, 2019, "Smith", "John", "10.1000/real.1")
    a = await _run(ref, [item])
    assert a.status == RefStatus.verified, (a.status, a.mismatches)


# ---------- strip_venue hides a negated / extra tail ----------


async def test_negated_tail_end_to_end_is_not_verified():
    # AGENTS.md: titles differing in negation words never match. The cited title adds a
    # second sentence that contradicts the record; strip_venue drops it (". In G..." looks
    # like a venue), decide() compares only the head, and the report says verified.
    cited = "Antidepressants reduce suicide risk in adolescents. In Girls, No Such Effect Is Seen"
    found = "Antidepressants reduce suicide risk in adolescents"
    ref = _ref(cited, 2018, "Lee, Anna")
    a = await _run(ref, [_crossref_item(found, 2018, "Lee", "Anna", "10.1000/x.2")])
    assert a.status != RefStatus.verified, a.note


# ---------- arXiv title-search fallback ----------

ENTRY = """<entry><id>http://arxiv.org/abs/{aid}v1</id>
<published>{year}-06-01T00:00:00Z</published><title>{title}</title>
<author><name>{author}</name></author><summary>x</summary></entry>"""


async def test_arxiv_near_title_other_authors_far_year_is_not_verified():
    # fabricated reference: an arXiv paper with the same title exists, by someone else, 6
    # years apart. Must never be verified (title alone identifies nothing).
    title = "A generative process for sampling contractive auto-encoders"
    entry = ENTRY.format(aid="1206.6434", year=2012, title=title, author="Salah Rifai")
    a = await _run(
        _ref(title, 2018, "Nguyen, T"), arxiv=lambda r: httpx.Response(200, text=FEED.format(entry))
    )
    assert a.status == RefStatus.unchecked, (a.status, a.note)


@pytest.mark.parametrize(
    "fail",
    [
        lambda r: httpx.Response(429, headers={"retry-after": "0"}),
        lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=r)),
    ],
    ids=["429", "timeout"],
)
async def test_arxiv_search_failure_is_unchecked(fail):
    a = await _run(_ref("A study of reconfigurable widgets in context", 2020, "Doe, J"), arxiv=fail)
    assert a.status == RefStatus.unchecked and "arxiv" in a.note, (a.status, a.note)


# ---------- widened possible match only ever yields unchecked ----------


@pytest.mark.parametrize(
    ("cited", "found", "fy"),
    [
        # prefix at any year, same first author: a different, longer paper
        (
            "Deep learning for medical imaging",
            "Deep learning for medical imaging is not ready",
            2009,
        ),
        # >= 80% content words, same year: a reworded title of possibly another paper
        (
            "Theano: a CPU and GPU math expression compiler",
            "Theano: A CPU and GPU Math Compiler in Python",
            2010,
        ),
    ],
)
def test_possible_match_is_never_verified(cited, found, fy):
    ref = _ref(cited, 2010, "Bergstra, J")
    works = [Work(provider="crossref", title=found, year=fy, authors=["Bergstra, James"])]
    a = refaudit.decide(ref, works, OK)
    assert a.status == RefStatus.unchecked and a.note.startswith("possible match"), a.note


async def test_arxiv_non_atom_200_is_unchecked():
    # a proxy / maintenance page served as 200 that happens to be well-formed XML (XHTML):
    # parse_arxiv finds no <entry> and reads it as "arXiv has no such paper" -> not_found
    page = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
        "<body><p>arXiv is temporarily unavailable.</p></body></html>"
    )
    a = await _run(
        _ref("A study of reconfigurable widgets in context", 2020, "Doe, J"),
        arxiv=lambda r: httpx.Response(200, text=page),
    )
    assert a.status == RefStatus.unchecked, (a.status, a.note)
