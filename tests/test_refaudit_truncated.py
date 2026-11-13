"""A truncated cited title (a prefix of the real title) must not become not_found."""

import json
from pathlib import Path

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work
from claimtrace.refaudit.match import identifies, truncated_title
from claimtrace.refaudit.providers import parse_crossref

FIX = Path(__file__).parent / "fixtures" / "refaudit"
FULL = "A Decomposable Attention Model for Natural Language Inference"

# b26 from the "Attention Is All You Need" PDF, as parsed
B26 = Reference(
    id="b26",
    raw="Ankur Parikh, Oscar Täckström, Dipanjan Das, and Jakob Uszkoreit. A decomposable "
    "attention model. In Empirical Methods in Natural Language Processing, 2016.",
    title="A decomposable attention model",
    authors=["Parikh, Ankur", "Täckström, Oscar", "Das, Dipanjan", "Uszkoreit, Jakob"],
    year=2016,
)
ANSWERED = [
    ProviderOutcome(provider="crossref", ok=True, candidates=2),
    ProviderOutcome(provider="openalex", ok=True, candidates=0),
]


@pytest.mark.parametrize(
    "cited, found, ok",
    [
        ("A decomposable attention model", FULL, True),
        (FULL, "A decomposable attention model", True),  # either side may be the shorter
        ("A decomposable attention model.", FULL.upper(), True),
        # 2-word prefix proves nothing
        ("Deep learning", "Deep learning for image recognition", False),
        # added words in the longer title bring a negation / number
        ("Vaccines cause autism", "Vaccines cause autism not", False),
        ("Statins reduce mortality", "Statins reduce mortality without side effects", False),
        ("Results of the survey", "Results of the survey 2020", False),
        # negation inside the shared part differs -> not a prefix at all
        ("Vaccines do cause autism", "Vaccines do not cause autism in children", False),
        # not a prefix (words differ in the middle)
        ("A decomposable attention network", FULL, False),
        # a correction notice is a different record
        ("A decomposable attention model", "Erratum: " + FULL, False),
    ],
)
def test_truncated_title(cited, found, ok):
    assert truncated_title(cited, found) is ok


def _work(title, year=2016, authors=("Parikh, Ankur",)):
    return Work(provider="crossref", title=title, year=year, authors=list(authors))


def test_truncated_title_needs_year_and_first_author():
    assert identifies(B26, _work(FULL))
    assert not identifies(B26, _work(FULL, year=2019))
    assert not identifies(B26, _work(FULL, authors=["Smith, John"]))


def test_decide_b26_truncated_title_is_metadata_mismatch():
    items = json.loads((FIX / "crossref_search_parikh.json").read_text())["message"]["items"]
    works = [parse_crossref(i) for i in items]
    a = refaudit.decide(B26, works, ANSWERED)
    assert a.status == RefStatus.metadata_mismatch
    assert a.matched.doi == "10.18653/v1/d16-1244"
    assert [(m.field, m.cited, m.found) for m in a.mismatches] == [("title", B26.title, FULL)]


def test_decide_negation_added_is_not_identified_but_not_not_found():
    w = _work("A decomposable attention model does not help natural language inference")
    a = refaudit.decide(B26, [w], ANSWERED)
    assert a.status == RefStatus.unchecked and a.note.startswith("possible match: ")


def test_decide_near_match_is_unchecked_never_not_found():
    # same first author and year, similar title that is neither a match nor a prefix
    w = _work("Decomposable attention models for natural language inference")
    a = refaudit.decide(B26, [w], ANSWERED)
    assert a.status == RefStatus.unchecked
    assert a.note == f"possible match: {w.title} (crossref, 2016)"


def test_decide_nothing_close_is_still_not_found():
    far = [
        _work("Decomposable attention for parsing", authors=["Smith, John"]),  # other author
        _work("Decomposable attention for parsing", year=2010),  # other year
        _work("Graph neural networks for chemistry"),  # unrelated title
    ]
    assert refaudit.decide(B26, far, ANSWERED).status == RefStatus.not_found
