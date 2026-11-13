"""The 'exactly one preprint' exception must not join a preprint of a *different* paper."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex", "arxiv")]


@pytest.mark.xfail(
    strict=True, reason="BUG: preprint whose own journal DOI differs is grouped as a version"
)
def test_preprint_published_elsewhere_is_not_a_version_of_the_cited_paper():
    t = "A survey of graph neural networks"
    # cited: Smith's 2020 journal survey, but with the arXiv id of Smith's 2012 preprint, which
    # arXiv says was published as a different (2013 IEEE) paper
    ref = Reference(
        id="1", raw="", title=t, authors=["Smith, J."], year=2020, arxiv_id="1203.04567"
    )
    pre = Work(
        provider="arxiv",
        title=t,
        authors=["John Smith"],
        year=2012,
        arxiv_id="1203.04567",
        doi="10.1109/tnn.2013.111",
        abstract="2012 abstract",
    )
    journal = Work(
        provider="crossref",
        title=t,
        authors=["Smith, John"],
        year=2020,
        doi="10.1016/j.neunet.2020.05.001",
    )
    a = refaudit.decide(ref, [pre, journal], OK)
    # today: verified, matched = 2020 journal DOI + the 2012 preprint's arXiv id and abstract
    assert a.status != RefStatus.verified
