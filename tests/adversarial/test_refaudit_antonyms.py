"""Adversarial: a cited title whose direction of effect is reversed (increases/decreases,
higher/lower, mice/humans) must not be verified, even at >= 0.9 title similarity."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]


@pytest.mark.parametrize(
    "cited, found",
    [
        (
            "Coffee consumption decreases the risk of type 2 diabetes in middle-aged women",
            "Coffee consumption increases the risk of type 2 diabetes in middle-aged women",
        ),
        (
            "Higher dietary fiber intake is associated with higher risk of colorectal cancer",
            "Higher dietary fiber intake is associated with lower risk of colorectal cancer",
        ),
        (
            "Intermittent fasting improves insulin sensitivity in obese humans",
            "Intermittent fasting improves insulin sensitivity in obese mice",
        ),
    ],
)
def test_reversed_title_is_not_verified(cited, found):
    ref = Reference(id="1", raw=cited, title=cited, year=2020, authors=["Smith J"])
    work = Work(provider="crossref", title=found, year=2020, authors=["Smith, John"], doi="10.1/x")
    assert refaudit.decide(ref, [work], OK).status != RefStatus.verified
