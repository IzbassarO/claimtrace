"""Live: real judge/skeptic providers and the real fastembed model. Runs only with RUN_LIVE=1."""

from claimtrace.claims import check_claim, retrieval
from claimtrace.claims.quote import verify_quote
from claimtrace.core.schemas import Citation, SourceText

ABSTRACT = (
    "In a randomised controlled trial of 400 adults with stage 1 hypertension, drug X reduced "
    "systolic blood pressure by 12 mmHg compared with placebo after 12 weeks. Adverse events "
    "were similar between groups. Effects in children were not studied."
)
SRC = SourceText(ref_id="1", kind="abstract", text=ABSTRACT, origin="openalex", url=None)


async def test_live_supported_claim_has_verified_quote():
    cit = Citation(
        id="c1", sentence="Drug X lowers systolic blood pressure in adults [1].", ref_ids=["1"]
    )
    res = await check_claim(cit, "1", SRC)
    assert res.judge and res.skeptic, res.note
    assert res.verdict in ("supported", "partly_supported")
    assert verify_quote(res.quote, ABSTRACT)


async def test_live_overstated_claim_is_not_supported():
    cit = Citation(id="c2", sentence="Drug X cures hypertension in children [1].", ref_ids=["1"])
    res = await check_claim(cit, "1", SRC)
    assert res.verdict != "supported"


async def test_live_fastembed_ranks_relevant_passage():
    filler = " ".join(f"Sentence {i} is about regional weather and rainfall." for i in range(80))
    text = filler + " Statins reduced LDL cholesterol by 40 percent in older adults. " + filler
    vecs = retrieval.fastembed_embed(["statin cholesterol", "Statins reduced LDL cholesterol."])
    assert len(vecs) == 2 and len(vecs[0]) == 384
    out = await retrieval.retrieve("statins lower cholesterol", text)
    assert any("Statins reduced LDL" in p for p in out)
