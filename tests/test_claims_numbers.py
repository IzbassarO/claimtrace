import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.claims import check_claim, retrieval
from claimtrace.claims.numbers import cap_numbers, claim_numbers
from claimtrace.core.schemas import Citation, SourceText

EXTRACT = [
    ("The dose was 10 mg per day.", ["10"]),
    ("Mortality fell by 50% (p < 0.05).", ["50%", "0.05"]),
    ("A 1.5-fold rise over 10–20 weeks.", ["1.5-fold", "10", "20"]),
    ("Lactate 5mmol/L in 1,000 kids, ±2.3 SD, 3x.", ["5mmol", "1,000", "2.3", "3x"]),
    ("BP dropped by -5 mmHg.", ["5"]),
    ("X reduces Y [12] and Z [3–5].", []),
    ("X reduces Y (Smith, 2020; Lee et al. 2019b).", []),
    ("As Smith et al. 2019 showed, X reduces Y.", []),
    ("See Fig. 2, Figure 3a, Table 3 and Section 4.", []),
    ("COVID-19, IL-6, CD4, H1N1, BRCA1, SARS-CoV-2 and Phase II trials.", []),
    ("Type 1 diabetes, grade 2 lesions, MEK 1/2 inhibitors, HPV 16.", []),
    ("Phosphatidylinositol-3,4-biphosphate and PI 3-kinase and 5-HT and 4T1 cells.", []),
    ("In 2010 the incidence rose.", []),
]  # fmt: skip


@pytest.mark.parametrize("sentence, expected", EXTRACT)
def test_claim_numbers(sentence, expected):
    assert [t for t, _ in claim_numbers(sentence)] == expected


Q = "In the trial, {} was observed in all treated patients."
CAP = [
    ("Drug X at 10 mg lowers BP.", Q.format("a dose of 110 mg"), "partly_supported"),
    ("Drug X at 10 mg lowers BP.", Q.format("a dose of 10 mg"), "supported"),
    ("Drug X at 10mg lowers BP.", Q.format("a dose of 10 mg"), "supported"),
    ("Mortality fell by 50%.", Q.format("a 50 percent fall"), "supported"),
    ("A 1.5-fold rise.", Q.format("a 1.5 fold rise"), "supported"),
    ("A 1.5-fold rise.", Q.format("a 15 fold rise"), "partly_supported"),
    ("Significant at p<0.05.", Q.format("an effect at p<0.01"), "partly_supported"),
    ("Significant at p<0.05.", Q.format("an effect at p < 0.050"), "supported"),
    ("Effect size 0.05.", Q.format("an effect of 0.5"), "partly_supported"),
    ("Onset under 3 hours.", Q.format("onset under three hours"), "supported"),
    ("X lowers BP [12], [3-5] (Smith, 2020).", Q.format("lower BP"), "supported"),
    ("X lowers COVID-19 mortality.", Q.format("lower mortality"), "supported"),
]  # fmt: skip


@pytest.mark.parametrize("sentence, quote, expected", CAP)
def test_cap(sentence, quote, expected):
    verdict, note = cap_numbers("supported", quote, sentence)
    assert verdict == expected
    assert (note is not None) == (expected != "supported")


def test_cap_note_and_untouched_verdicts():
    q = Q.format("a dose of 110 mg")
    assert cap_numbers("supported", q, "At 10 mg.") == (
        "partly_supported",
        "number in the claim (10) not found in the quoted evidence",
    )
    for v in ("partly_supported", "unsupported", "insufficient_evidence"):
        assert cap_numbers(v, q, "At 10 mg.") == (v, None)
    assert cap_numbers("supported", None, "At 10 mg.") == ("supported", None)


@pytest.fixture(autouse=True)
def _no_model_download(monkeypatch):
    def boom(_texts):
        raise RuntimeError("unit tests must not load fastembed")

    monkeypatch.setattr(retrieval, "fastembed_embed", boom)


def fake(verdict, quote):
    reply = json.dumps({"verdict": verdict, "quote": quote, "rationale": "r"})
    return FakeListChatModel(responses=[reply])


@pytest.mark.parametrize(
    "sentence, verdict, has_note",
    [
        ("Drug X lowers systolic pressure by 12 mmHg [1].", "supported", False),
        ("Drug X lowers systolic pressure by 20 mmHg [1].", "partly_supported", True),
    ],
)
async def test_check_claim_applies_cap(sentence, verdict, has_note):
    quote = "drug X reduced systolic blood pressure by 12 mmHg"
    src = SourceText(ref_id="1", kind="abstract", text=f"In adults, {quote}.", origin="openalex")
    cit = Citation(id="c1", sentence=sentence, ref_ids=["1"])
    res = await check_claim(
        cit, "1", src, judge_model=fake("supported", quote), skeptic_model=fake("supported", quote)
    )
    assert res.verdict == verdict and res.quote == quote
    assert ("number in the claim (20)" in (res.note or "")) == has_note
