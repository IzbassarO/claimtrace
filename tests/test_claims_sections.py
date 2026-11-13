"""Section detection and the verified quote's section label (offline)."""

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
