"""Adversarial: retraction/withdrawal markers that refaudit misses, reporting `verified`."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus
from claimtrace.refaudit.providers import parse_crossref, parse_s2

T = (
    "Effects of intensive blood pressure control on cardiovascular outcomes "
    "in adults with type 2 diabetes mellitus: a randomised trial"
)
REF = Reference(id="1", raw="", title=T, year=2020, authors=["Smith J"])
ALL_OK = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]


@pytest.mark.parametrize("prefix", ["WITHDRAWN: ", "RETRACTED ARTICLE: ", "Retracted - "])
def test_retraction_title_prefix_is_flagged(prefix):
    w = parse_s2(
        {"paperId": "p", "title": prefix + T, "year": 2020, "authors": [{"name": "J Smith"}]}
    )
    assert refaudit.decide(REF, [w], ALL_OK).status == RefStatus.retracted  # today: verified


def test_crossref_withdrawal_update_is_flagged():
    item = {
        "DOI": "10.1/w",
        "title": [T],
        "author": [{"family": "Smith", "given": "John"}],
        "issued": {"date-parts": [[2020]]},
        "updated-by": [{"type": "withdrawal", "DOI": "10.1/w-notice"}],
    }
    a = refaudit.decide(REF, [parse_crossref(item)], ALL_OK)
    assert a.status == RefStatus.retracted  # today: verified
