import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `eval` is a top-level folder, not an installed package

from claimtrace.core.schemas import ClaimCheck, EvidenceKind, Verdict  # noqa: E402
from eval import batch_claims  # noqa: E402

TEXT = "Title\n\nAspirin lowers the risk of stroke in older adults by a third."
QUOTE = "Aspirin lowers the risk of stroke"


def item(claim_id, doc_id, gold="supported"):
    return {
        "claim_id": claim_id,
        "doc_id": doc_id,
        "claim": f"claim {claim_id}",
        "gold": gold,
        "kind": EvidenceKind.abstract,
        "text": TEXT,
    }


def test_groups_share_a_doc_have_two_to_four_claims_and_reach_the_limit():
    items = [item(i, 1) for i in range(6)] + [item(10, 2)] + [item(20, 3), item(21, 3)]
    groups = batch_claims.make_groups(items, limit=100, seed=0)
    assert sorted(len(g) for g in groups) == [2, 2, 4]  # doc 1 split 4 + 2, doc 2 dropped
    assert all(len({i["doc_id"] for i in g}) == 1 for g in groups)
    assert sum(map(len, batch_claims.make_groups(items, limit=3, seed=0))) >= 3


async def test_run_mode_rows_and_failures(monkeypatch):
    def ok(c, r):
        return ClaimCheck(
            citation_id=c.id, ref_id=r, sentence=c.sentence, verdict=Verdict.supported, quote=QUOTE
        )

    async def single(c, r, src):
        return ok(c, r)

    async def batched(items, src):
        if items[0][0].id == "sf3":
            raise RuntimeError("boom")
        return [ok(c, r) for c, r in items]

    monkeypatch.setattr(batch_claims.claims, "check_claim", single)
    monkeypatch.setattr(batch_claims.claims, "check_claims", batched)
    groups = [[item(1, 1), item(2, 1, "nei")], [item(3, 2), item(4, 2)]]
    per = await batch_claims.run_mode(groups, batched=False)
    assert per["metrics"]["accuracy"] == 0.75 and per["metrics"]["quote_verified_rate"] == 1
    bat = await batch_claims.run_mode(groups, batched=True)
    failed = [r for r in bat["rows"] if r["error"]]
    # a crashed batch is insufficient evidence, never a verdict
    assert [r["verdict"] for r in failed] == ["insufficient_evidence"] * 2
    assert bat["metrics"]["errors"] == 2 and bat["metrics"]["llm_calls"] == 0
