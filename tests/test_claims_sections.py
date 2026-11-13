"""Paragraph/section-aware passages and the quote's section label (offline)."""

import json
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.claims import check_claim, retrieval
from claimtrace.core.schemas import Citation, SourceText

PAPER = (Path(__file__).parent / "fixtures" / "claims" / "sectioned_paper.txt").read_text()
RESULT = "Mean systolic blood pressure decreased by 6.2 mmHg in the walking group"


def test_sections_detected_and_front_matter_is_not_a_heading():
    secs = [s for s, _ in retrieval.paragraphs(PAPER)]
    assert secs[:4] == [None] * 4  # title, surnames, "Abstract", abstract text: front matter
    assert set(secs) == {
        None,
        "1 Introduction",
        "2.1 Participants",
        "2.2 Intervention",
        "3 Results",
        "4 Discussion",
    }
    paras = dict((p[:20], s) for s, p in retrieval.paragraphs(PAPER))
    assert paras["Of the 240 participa"] == "3 Results"
    assert paras["Baseline characteris"] is None  # caption title is not a section
    assert paras["Attendance averaged "] is None  # after an inline table: unknown, not guessed


def test_quote_in_results_is_labelled_results():
    assert retrieval.section_of(RESULT, PAPER) == "3 Results"
    assert retrieval.section_of("exercise lowers blood pressure in younger", PAPER) == (
        "1 Introduction"
    )
    assert retrieval.section_of("Systolic blood pressure fell modestly in the", PAPER) is None
    assert retrieval.section_of("not in the paper at all here", PAPER) is None


def test_quote_in_two_sections_is_not_labelled():
    text = "Intro\n\nfirst para.\n\nResults\n\nthe same five words here.\n\nDiscussion\n\n"
    text += "the same five words here."
    assert retrieval.section_of("the same five words here", text) is None


def test_passages_stay_in_paragraphs_and_overlap_only_within_a_section():
    ps = retrieval.passages(PAPER)
    assert not any(p in {"3 Results", "2 Methods", "4 Discussion"} for p in ps)
    intro2 = next(p for p in ps if p.startswith("Drug treatment is effective"))
    assert "Exercise lowers blood pressure" in intro2  # carried into the next intro paragraph
    assert next(p for p in ps if "We recruited 240" in p).startswith("We recruited 240")
    first_results = next(p for p in ps if "Of the 240 participants" in p)
    assert first_results.startswith("Of the 240")  # no sentence carried over from Methods


def test_no_blank_lines_keeps_old_windows():
    text = " ".join(f"S{i} ends here." for i in range(7))
    assert retrieval.passages(text) == [
        "S0 ends here. S1 ends here. S2 ends here.",
        "S2 ends here. S3 ends here. S4 ends here.",
        "S4 ends here. S5 ends here. S6 ends here.",
    ]


async def test_check_claim_sets_section_only_for_a_verified_quote():
    cit = Citation(id="c1", sentence="Walking lowers systolic pressure [1].", ref_ids=["1"])
    src = SourceText(ref_id="1", kind="full_text", text=PAPER, origin="europepmc", url="u")

    def fake(quote):
        reply = {"verdict": "supported", "quote": quote, "rationale": "r"}
        return FakeListChatModel(responses=[json.dumps(reply)])

    res = await check_claim(cit, "1", src, judge_model=fake(RESULT), skeptic_model=fake(RESULT))
    assert res.verdict == "supported" and res.section == "3 Results"
    bad = "walking cured hypertension in every participant"
    res = await check_claim(cit, "1", src, judge_model=fake(bad), skeptic_model=fake(bad))
    assert res.verdict == "insufficient_evidence" and res.section is None
