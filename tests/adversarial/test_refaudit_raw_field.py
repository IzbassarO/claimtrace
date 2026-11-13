"""_in_raw: a sub-span of the cited title is not the title field."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.match import _in_raw


@pytest.mark.xfail(strict=True, reason="BUG: a subtitle / main title / post-year span counts")
@pytest.mark.parametrize(
    ("raw", "title"),
    [
        (
            "Rahman A. Artificial intelligence in sepsis: challenges and opportunities. "
            "Crit Care. 2020;24:1.",
            "Challenges and opportunities",
        ),
        (
            "Smith J. Does aspirin reduce mortality? A cohort study of adults. Lancet. 2019.",
            "Does aspirin reduce mortality?",
        ),
        (
            "Smith J. Lessons from the 2014 Ebola virus outbreak. Lancet. 2015.",
            "Ebola virus outbreak",
        ),
    ],
)
def test_part_of_the_cited_title_is_not_a_field(raw, title):
    assert not _in_raw(raw, title)


@pytest.mark.xfail(
    strict=True, reason="BUG: DOI of another paper verified because its title is the cited subtitle"
)
def test_wrong_doi_with_subtitle_title_is_not_verified():
    raw = (
        "Rahman A. Artificial intelligence in sepsis: challenges and opportunities. "
        "Crit Care. 2020;24:1. doi:10.1186/s13054-020-0001-1"
    )
    # title dropped by extraction; the cited DOI is the author's other 2020 paper
    ref = Reference(
        id="1", raw=raw, authors=["Rahman A"], year=2020, doi="10.1186/s13054-020-0001-1"
    )
    other = Work(
        provider="crossref",
        title="Challenges and opportunities",
        authors=["Rahman, Ayesha"],
        year=2020,
        doi="10.1186/s13054-020-0001-1",
    )
    ok = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]
    assert refaudit.decide(ref, [other], ok).status != RefStatus.verified
