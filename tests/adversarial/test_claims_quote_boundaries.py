"""Adversarial: quotes that pass the verbatim gate while saying something the source does not."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.claims import check_claim
from claimtrace.claims.quote import verify_quote
from claimtrace.core.schemas import Citation, SourceText

# ---------- bug 1: second half of a word broken across a line counts as a whole word ----------

LINEBREAK_CASES = [
    # hard hyphen at the line end (PDF / GROBID text): "un-\nchanged" is one word, "unchanged"
    (
        "changed in the treatment group at follow-up",
        "Blood pressure remained un-\nchanged in the treatment group at follow-up.",
    ),
    # soft hyphen + newline: the soft hyphen is dropped, the newline becomes a space
    (
        "effective in all patients we treated",
        "The drug was in­\neffective in all patients we treated.",
    ),
]


@pytest.mark.parametrize(("quote", "source"), LINEBREAK_CASES)
def test_quote_cannot_start_inside_a_line_broken_word(quote, source):
    assert not verify_quote(quote, source)


async def test_line_broken_negation_gives_supported_verdict():
    quote, text = LINEBREAK_CASES[0]
    cit = Citation(id="c1", sentence="Drug X changed blood pressure [1].", ref_ids=["1"])
    src = SourceText(ref_id="1", kind="full_text", text=text, origin="europepmc")
    reply = json.dumps({"verdict": "supported", "quote": quote, "rationale": "r"})
    res = await check_claim(
        cit,
        "1",
        src,
        judge_model=FakeListChatModel(responses=[reply]),
        skeptic_model=FakeListChatModel(responses=[reply]),
    )
    assert res.verdict == "insufficient_evidence" and res.quote is None


# ---------- bug 2: a number cut off before a glue character the gate does not know ----------


@pytest.mark.parametrize(
    ("quote", "source"),
    [
        # "5" vs "5x" (fold change); "5-fold" is already rejected
        ("the risk of death increased 5", "the risk of death increased 5× in smokers"),
        # "1" vs "1/2" written with the fraction slash
        ("the observed reduction was 1", "the observed reduction was 1⁄2 of baseline"),
        # ranges: "10" vs "10~20" / "10-20" with figure dash / em dash; en dash is rejected
        ("patients received doses of 10", "patients received doses of 10~20 mg daily"),
        ("doses were titrated up to 10", "doses were titrated up to 10‒20 mg"),
        ("doses were titrated up to 10", "doses were titrated up to 10—20 mg"),
    ],
)
def test_quote_cannot_end_inside_a_number(quote, source):
    assert not verify_quote(quote, source)


# ---------- bug 3: a minus sign separated by a space is dropped from the quote ----------


def test_quote_cannot_drop_a_spaced_minus_sign():
    # "-5" and "−5" are rejected already; PDF text often puts a space after the minus
    source = "The mean change was − 5 mmHg in all treated patients."
    assert not verify_quote("5 mmHg in all treated patients", source)
