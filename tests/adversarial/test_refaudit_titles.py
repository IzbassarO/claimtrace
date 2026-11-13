"""Adversarial: title matching in refaudit lets a different paper through as `verified`, or
calls a real paper `not_found`. Pure decide() with every provider answering."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.providers import parse_crossref

ALL_OK = [ProviderOutcome(provider=p, ok=True) for p in ("crossref", "openalex")]


def audit(cited: str, found: str):
    ref = Reference(id="1", raw=cited, title=cited, year=2020, authors=["Smith J"])
    work = Work(provider="crossref", title=found, year=2020, authors=["Smith, John"], doi="10.1/x")
    return refaudit.decide(ref, [work], ALL_OK)


@pytest.mark.parametrize(
    "cited, found",
    [
        (
            "Lack of association between coffee consumption and pancreatic cancer risk",
            "Association between coffee consumption and pancreatic cancer risk",
        ),
        (
            "Absence of association between coffee consumption and pancreatic cancer risk",
            "Association between coffee consumption and pancreatic cancer risk",
        ),
        (
            "Statins fail to reduce cardiovascular mortality in elderly women",
            "Statins reduce cardiovascular mortality in elderly women",
        ),
        (
            "Nonsignificant effect of aspirin on stroke recurrence in older adults",
            "Significant effect of aspirin on stroke recurrence in older adults",
        ),
    ],
)
def test_negated_title_is_not_verified(cited, found):
    assert audit(cited, found).status != RefStatus.verified


@pytest.mark.parametrize(
    "cited, found",
    [
        (
            "Results of a phase II trial of pembrolizumab in advanced melanoma",
            "Results of a phase III trial of pembrolizumab in advanced melanoma",
        ),
        (
            "Two-year outcomes of transcatheter aortic valve replacement in low-risk patients",
            "Five-year outcomes of transcatheter aortic valve replacement in low-risk patients",
        ),
    ],
)
def test_number_changed_title_is_not_verified(cited, found):
    assert audit(cited, found).status != RefStatus.verified


def test_different_subtitle_is_not_verified():
    a = audit(
        "Deep learning in medical imaging: opportunities and promises",
        "Deep learning in medical imaging: pitfalls and limitations",
    )
    assert a.status != RefStatus.verified


@pytest.mark.parametrize("prefix", ["Correction to: ", "Erratum: "])
def test_correction_notice_is_not_the_cited_paper(prefix):
    t = "Effects of intensive blood pressure control in type 2 diabetes mellitus patients"
    assert audit(t, prefix + t).status != RefStatus.verified


def test_subscript_markup_in_crossref_title_still_matches():
    item = {
        "DOI": "10.1/co2",
        "title": ["Elevated CO<sub>2</sub> reduces the nutritional quality of wheat grain"],
        "author": [{"family": "Smith", "given": "John"}],
        "issued": {"date-parts": [[2020]]},
    }
    ref = Reference(
        id="1",
        raw="",
        title="Elevated CO2 reduces the nutritional quality of wheat grain",
        year=2020,
        authors=["Smith J"],
    )
    a = refaudit.decide(ref, [parse_crossref(item)], ALL_OK)
    assert a.status == RefStatus.verified  # today: not_found


def test_omitted_subtitle_with_number_still_matches():
    a = audit(
        "Mortality after cardiac surgery in the elderly",
        "Mortality after cardiac surgery in the elderly: a 10-year cohort study",
    )
    assert a.status == RefStatus.verified  # today: not_found
