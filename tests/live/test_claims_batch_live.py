"""Live: one batched judge + skeptic call over two claims. Runs only with RUN_LIVE=1."""

from claimtrace.claims import check_claims
from claimtrace.claims.quote import verify_quote
from claimtrace.core.schemas import Citation, SourceText

ABSTRACT = (
    "In a randomised controlled trial of 400 adults with stage 1 hypertension, drug X reduced "
    "systolic blood pressure by 12 mmHg compared with placebo after 12 weeks. Adverse events "
    "were similar between groups. Effects in children were not studied."
)
SRC = SourceText(ref_id="1", kind="abstract", text=ABSTRACT, origin="openalex", url=None)


async def test_live_batch_keeps_per_claim_verdicts_apart():
    good = Citation(
        id="c1", sentence="Drug X lowers systolic blood pressure in adults [1].", ref_ids=["1"]
    )
    bad = Citation(id="c2", sentence="Drug X cures hypertension in children [1].", ref_ids=["1"])
    a, b = await check_claims([(good, "1"), (bad, "1")], SRC)
    assert (a.citation_id, b.citation_id) == ("c1", "c2")
    assert a.judge and a.skeptic, a.note
    assert a.verdict in ("supported", "partly_supported") and verify_quote(a.quote, ABSTRACT)
    assert b.verdict != "supported"
