"""Two different papers with one generic title must not be merged into one work."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]


@pytest.mark.xfail(
    strict=True,
    reason="BUG: same first author + same title groups papers 7 years apart with distinct DOIs",
)
def test_same_author_same_title_other_year_lends_nothing():
    # one guest editor, two special-issue introductions (2012 and 2019), two journal DOIs
    t = "Introduction to the special issue on machine learning"
    ref = Reference(id="1", raw="", title=t, authors=["Smith, J."], year=2019)
    cited = Work(
        provider="crossref", title=t, authors=["Smith, John"], year=2019, doi="10.1016/j.ml.2019.1"
    )
    other = Work(
        provider="openalex",
        title=t,
        authors=["John Smith"],
        year=2012,
        doi="10.1007/s10994-012-5300-1",
        abstract="Abstract of the 2012 introduction.",
        is_retracted=True,
    )
    a = refaudit.decide(ref, [cited, other], OK)
    assert a.status != RefStatus.retracted  # the 2019 paper is not retracted
    assert a.matched.abstract is None  # the claims stage would read the 2012 abstract


@pytest.mark.xfail(
    strict=True,
    reason="BUG: an authorless 'Deep learning' (LeCun 2015) record is grouped with Goodfellow 2016",
)
def test_authorless_same_title_record_lends_no_doi():
    ref = Reference(id="1", raw="", title="Deep learning", authors=["Goodfellow, I."], year=2016)
    book = Work(
        provider="openalex",
        title="Deep learning",
        authors=["Ian Goodfellow", "Yoshua Bengio", "Aaron Courville"],
        year=2016,
    )
    nature = Work(
        provider="semanticscholar",
        title="Deep learning",
        year=2015,
        doi="10.1038/nature14539",
        abstract="LeCun et al. abstract",
    )
    a = refaudit.decide(ref, [book, nature], OK)
    # reported verified with LeCun's DOI and abstract attached to Goodfellow's book
    assert a.matched.doi != "10.1038/nature14539"
    assert a.matched.abstract is None
