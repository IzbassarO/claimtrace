"""Adversarial: the truncated-title path and the "possible match" safety net from PR #35.
Pure decide() with every provider answering, candidates shaped like a Crossref search
(query.bibliographic = title + first author's family name + year, rows=5)."""

import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import ProviderOutcome, Reference, RefStatus, Work

OK = [
    ProviderOutcome(provider="crossref", ok=True, candidates=5),
    ProviderOutcome(provider="openalex", ok=True, candidates=0),
]


def _work(title, year, author, doi=None):
    return Work(provider="crossref", title=title, year=year, authors=[author], doi=doi)


def test_ambiguous_truncated_title_is_not_identified():
    ref = Reference(
        id="b1",
        raw="Zhang L. Deep learning for sentiment analysis. 2018.",
        title="Deep learning for sentiment analysis",
        authors=["Zhang, Lei"],
        year=2018,
    )
    works = [
        _work(
            "Deep learning for sentiment analysis of product reviews in e-commerce platforms",
            2018,
            "Zhang, Lei",
            "10.1/a",
        ),
        _work("Deep learning for sentiment analysis of Chinese microblogs", 2018, "Zhang, Lei"),
    ]
    a = refaudit.decide(ref, works, OK)
    # today: metadata_mismatch, matched = the shorter-titled one (by similarity alone), and the
    # claims stage reads that paper as the cited source
    assert a.status == RefStatus.unchecked
    assert a.matched is None


# fabricated (LLM-style) references with a common surname; the only same-surname, same-year
# hit in a typical top-5 is a paper on an unrelated topic
FABRICATED = [
    (
        "A review of deep learning in medical imaging",
        "A review of machine learning in materials science",
    ),
    (
        "Deep learning for protein structure prediction",
        "Deep learning for weather forecasting in coastal regions",
    ),
]


@pytest.mark.parametrize("cited, other", FABRICATED)
def test_fabricated_reference_with_common_surname_is_not_found(cited, other):
    ref = Reference(
        id="b2", raw=f"Wang L (2021) {cited}.", title=cited, authors=["Wang, Lei"], year=2021
    )
    works = [
        _work(other, 2021, "Wang, Li"),
        _work("Deep learning in medical image analysis", 2017, "Shen, Dinggang"),
        _work("A survey on deep learning in medical image analysis", 2017, "Litjens, Geert"),
        _work("Medical imaging with deep learning: a review", 2020, "Chen, Xi"),
        _work("Convolutional networks for biomedical image segmentation", 2015, "Ronneberger, O"),
    ]
    # today: unchecked, "possible match: A review of machine learning in materials science"
    assert refaudit.decide(ref, works, OK).status == RefStatus.not_found


def test_truncated_title_without_year_is_not_not_found():
    ref = Reference(
        id="b26",
        raw="Ankur Parikh, Oscar Täckström, Dipanjan Das, and Jakob Uszkoreit. A decomposable "
        "attention model. In Empirical Methods in Natural Language Processing.",
        title="A decomposable attention model",
        authors=["Parikh, Ankur", "Täckström, Oscar", "Das, Dipanjan", "Uszkoreit, Jakob"],
    )
    works = [
        _work(
            "A Decomposable Attention Model for Natural Language Inference",
            2016,
            "Parikh, Ankur",
            "10.18653/v1/d16-1244",
        )
    ]
    # today: not_found ("fabricated") although the real paper is the only candidate
    assert refaudit.decide(ref, works, OK).status != RefStatus.not_found
