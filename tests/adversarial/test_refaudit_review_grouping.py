"""Post-review sweep: `compare()` lets any record of "the same work" agree, but `_same_work`
groups by title alone, and a record with a shorter author list / only an arXiv DOI / a title
that is a sub-span of the raw string "agrees" without confirming anything."""

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [
    ProviderOutcome(provider="crossref", ok=True, candidates=1),
    ProviderOutcome(provider="openalex", ok=True, candidates=1),
]
# two different works with the same title
LECUN = Work(
    provider="crossref",
    title="Deep learning",
    authors=["LeCun, Yann", "Bengio, Yoshua", "Hinton, Geoffrey"],
    year=2015,
    doi="10.1038/nature14539",
    abstract="Deep learning allows computational models ...",
)
BOOK = Work(
    provider="openalex",
    title="Deep Learning",
    authors=["Ian Goodfellow", "Yoshua Bengio", "Aaron Courville"],
    year=2016,
)


def test_wrong_year_not_rescued_by_another_paper_with_the_same_title():
    # LeCun et al. is 2015; cited 2017 is off by 2. Only Goodfellow's book (2016) "agrees".
    ref = Reference(
        id="1",
        raw="",
        title="Deep learning",
        authors=["LeCun, Y.", "Bengio, Y.", "Hinton, G."],
        year=2017,
        doi="10.1038/nature14539",
    )
    a = refaudit.decide(ref, [LECUN, BOOK], OK)
    assert a.status == RefStatus.metadata_mismatch
    assert "year" in {m.field for m in a.mismatches}


def test_doi_of_a_different_same_title_paper_is_not_verified():
    # Goodfellow's book cited with the DOI of LeCun's Nature paper
    ref = Reference(
        id="1",
        raw="",
        title="Deep learning",
        authors=["Goodfellow, I.", "Bengio, Y.", "Courville, A."],
        year=2016,
        doi="10.1038/nature14539",
    )
    a = refaudit.decide(ref, [LECUN, BOOK], OK)
    assert a.status != RefStatus.verified
    assert "doi" in {m.field for m in a.mismatches}
    # and the source fetched for claims must not be LeCun's paper
    assert a.matched is None or a.matched.abstract != LECUN.abstract


def test_retraction_of_another_same_title_paper_does_not_taint_the_citation():
    ref = Reference(
        id="1",
        raw="",
        title="Deep learning",
        authors=["LeCun, Y.", "Bengio, Y.", "Hinton, G."],
        year=2015,
        doi="10.1038/nature14539",
    )
    other = Work(
        provider="openalex",
        title="Deep learning",
        authors=["Paper, Mill"],
        year=2016,
        doi="10.9999/pm.2016.1",
        is_retracted=True,
    )
    a = refaudit.decide(ref, [LECUN, other], OK)
    assert a.status == RefStatus.verified


T = "Deep residual learning for image recognition"
CR = Work(
    provider="crossref",
    title=T,
    authors=["He, Kaiming", "Zhang, Xiangyu", "Ren, Shaoqing", "Sun, Jian"],
    year=2016,
    doi="10.1109/cvpr.2016.90",
)


def test_fabricated_coauthor_not_hidden_by_a_record_with_fewer_authors():
    ref = Reference(
        id="1", raw="", title=T, authors=["He, K.", "Fakeman, Q."], year=2016, doi=CR.doi
    )
    short = CR.model_copy(update={"provider": "openalex", "authors": ["Kaiming He"]})
    assert refaudit.decide(ref, [CR], OK).status == RefStatus.metadata_mismatch  # alone: caught
    a = refaudit.decide(ref, [CR, short], OK)
    assert a.status == RefStatus.metadata_mismatch
    assert [(m.field, m.cited) for m in a.mismatches] == [("authors", "Fakeman, Q.")]


def test_fabricated_doi_not_verified_when_only_an_arxiv_record_is_found():
    # the cited DOI resolves nowhere (Crossref/OpenAlex 404); the title search finds only the
    # arXiv record, so the DOI is never compared, yet the reference is shown as verified
    ref = Reference(
        id="1",
        raw="",
        title="Attention is all you need",
        authors=["Vaswani, A.", "Shazeer, N."],
        year=2017,
        doi="10.1145/9999999.1234567",
    )
    ax = Work(
        provider="openalex",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani", "Noam Shazeer"],
        year=2017,
        doi="10.48550/arxiv.1706.03762",
    )
    a = refaudit.decide(ref, [ax], OK)
    assert a.status != RefStatus.verified


def test_found_title_inside_a_longer_cited_title_is_not_a_title_check():
    # title extraction failed (LLM outage); the DOI resolves to a different, shorter-titled
    # paper whose title happens to be a word span of the cited title
    ref = Reference(
        id="1",
        raw="Smith J, Lee K. Machine learning for early detection of sepsis in intensive care "
        "units. Crit Care Med. 2019. doi:10.1/x",
        year=2019,
        doi="10.1/x",
    )
    w = Work(
        provider="crossref",
        title="Early detection of sepsis",
        authors=["Brown, Alice"],
        year=2019,
        doi="10.1/x",
    )
    a = refaudit.decide(ref, [w], OK)
    assert a.status != RefStatus.verified
