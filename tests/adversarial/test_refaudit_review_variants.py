"""Post-review sweep: correct citations of real papers reported as negative findings by the
stricter title rules / possible-match rule."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.match import title_match

OK = [
    ProviderOutcome(provider="crossref", ok=True, candidates=1),
    ProviderOutcome(provider="openalex", ok=True, candidates=1),
]


@pytest.mark.xfail(strict=True, reason="BUG: Greek letter vs its name (TNF-α/TNF-alpha) mismatch")
@pytest.mark.parametrize(
    ("cited", "found"),
    [
        (
            "TNF-alpha promotes inflammation in rheumatoid arthritis",
            "TNF-α promotes inflammation in rheumatoid arthritis",
        ),  # noqa: E501
        ("Role of interferon-gamma in host defense", "Role of interferon-γ in host defense"),
        ("Beta-blockers after myocardial infarction", "β-Blockers after myocardial infarction"),
    ],
)
def test_greek_letter_spelled_out_is_the_same_title(cited, found):
    assert title_match(cited, found)


@pytest.mark.xfail(strict=True, reason="BUG: correct DOI + Greek-letter spelling => title mismatch")
def test_greek_letter_spelling_is_not_a_metadata_mismatch():
    w = Work(
        provider="crossref",
        title="TNF-α promotes inflammation in rheumatoid arthritis",
        authors=["Smith, John"],
        year=2010,
        doi="10.1/tnf",
    )
    ref = Reference(
        id="1",
        raw="",
        title="TNF-alpha promotes inflammation in rheumatoid arthritis",
        authors=["Smith J"],
        year=2010,
        doi="10.1/tnf",
    )
    assert refaudit.decide(ref, [w], OK).status == RefStatus.verified


@pytest.mark.xfail(strict=True, reason="BUG: real paper not_found when cited authors are missing")
def test_near_identical_title_same_year_without_authors_is_not_not_found():
    # a reference without authors (sloppy list, .bib entry without `author`); a one-letter typo
    # in the title, same year: "nothing close exists" is false
    w = Work(
        provider="crossref",
        title="Deep residual learning for image recognition",
        authors=["He, Kaiming"],
        year=2016,
        doi="10.1109/cvpr.2016.90",
    )
    ref = Reference(
        id="1",
        raw="Deep residual learning for image recogntion. In: CVPR 2016.",
        title="Deep residual learning for image recogntion",
        year=2016,
    )
    a = refaudit.decide(ref, [w], OK)
    assert a.status != RefStatus.not_found
