"""Rules from the second review: unresolved cited DOI, title as a whole raw field."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.match import _in_raw

T = "Attention is all you need"
AX = Work(
    provider="openalex",
    title=T,
    authors=["Ashish Vaswani"],
    year=2017,
    doi="10.48550/arxiv.1706.03762",
)
REF = Reference(id="1", raw="", title=T, authors=["Vaswani, A."], year=2017, doi="10.1145/9.9")


def test_unresolved_doi_after_failed_lookup_is_no_mismatch_and_not_verified():
    outcomes = [
        ProviderOutcome(provider="crossref", ok=False, error="HTTP 503"),
        ProviderOutcome(provider="openalex", ok=True, candidates=1),
    ]
    a = refaudit.decide(REF, [AX], outcomes)
    assert a.status == RefStatus.unchecked and not a.mismatches
    assert "cited DOI not confirmed; provider failed: crossref" in a.note


def test_unresolved_doi_after_answered_lookups_is_a_doi_mismatch():
    outcomes = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]
    a = refaudit.decide(REF, [AX], outcomes)
    assert a.status == RefStatus.metadata_mismatch
    assert [(m.field, m.found) for m in a.mismatches] == [("doi", "does not resolve")]


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ('J. Smith, "Aspirin reduces mortality," Lancet, 2019.', True),
        ("Smith J (2019) Aspirin reduces mortality. Lancet 1:2", True),
        ("Smith J. 2019 Aspirin reduces mortality [Internet].", True),
        ("Smith J. Low-dose aspirin reduces mortality. Lancet. 2019.", False),
        ("Smith J. Aspirin reduces mortality in the elderly. Lancet. 2019.", False),
        ("Smith J. No. No free lunch for aspirin. Lancet. 2019.", False),
    ],
)
def test_title_must_be_a_whole_field_of_the_raw(raw, ok):
    title = "No free lunch for aspirin" if "lunch" in raw else "Aspirin reduces mortality"
    assert _in_raw(raw, title) is ok
