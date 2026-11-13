"""Adversarial: citation markers attached to the wrong reference (or silently dropped)."""

import pytest

from claimtrace.ingest.extract import regex_reference
from claimtrace.ingest.text import find_citations


def ref(ref_id: str, raw: str, first_author: str | None = None):
    r = regex_reference(ref_id, raw)
    if first_author:
        r.authors = [first_author]
    return r


def test_three_author_narrative_not_attached_to_second_authors_paper():
    refs = [
        ref("jones", "Jones, A., Brown, B., & Smith, C. (2020). Title X. J Biol.", "Jones, A."),
        ref("brown", "Brown, B., & Lee, D. (2020). Title Y. J Chem.", "Brown, B."),
    ]
    (c,), _ = find_citations("Jones, Brown and Smith (2020) showed X.", refs)
    assert "brown" not in c.ref_ids  # Brown-led paper is a different work


def test_interval_zero_one_is_not_a_citation():
    refs = [ref("1", "Doe A. Sampling methods. J Stat. 2019;1:1-9.")]
    cites, _ = find_citations("We draw x uniformly from [0, 1] for every trial.", refs)
    assert all("1" not in c.ref_ids for c in cites)


@pytest.mark.parametrize(
    "text",
    [
        "Aspirin reduces mortality. [1] Statins have no effect on mortality. [2]",
        "Aspirin reduces mortality.[1] Statins have no effect on mortality.[2]",
    ],
)
def test_post_period_marker_stays_with_its_sentence(text):
    refs = [ref(n, f"Doe A. Trial {n}. Lancet. 2019;1:{n}.") for n in ("1", "2")]
    cites, _ = find_citations(text, refs)
    for c in cites:
        if "1" in c.ref_ids:
            assert "Statins" not in c.sentence  # ref 1 would be judged on the statin claim


@pytest.mark.parametrize("text", ["Shown twice (Smith, 2020a, b).", "Smith (2020a, b) showed."])
def test_year_suffix_shorthand_cites_both(text):
    refs = [
        ref("a", "Smith, J. (2020a). One. J Biol.", "Smith, J."),
        ref("b", "Smith, J. (2020b). Two. J Biol.", "Smith, J."),
    ]
    cites, _ = find_citations(text, refs)
    assert [c.ref_ids for c in cites] == [["a", "b"]]
