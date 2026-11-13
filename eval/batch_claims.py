"""Per-claim (`claims.check_claim`) vs batched (`claims.check_claims`) on the same SciFact dev
claims, grouped by cited abstract (only abstracts cited by >= 2 claims).

    uv run --env-file .env python -m eval.batch_claims [--limit 60] [--seed 0]

Writes eval/results/batch_claims.json (metrics per mode + rows). LLM calls are counted with
core.timing (retries included); both modes allow `llm_concurrency` requests in flight.
"""

import argparse
import asyncio
import json
import os
import random
import time
from datetime import UTC, datetime

from claimtrace import claims
from claimtrace.claims.quote import verify_quote
from claimtrace.core import timing
from claimtrace.core.schemas import Citation, SourceText
from claimtrace.core.settings import get_settings
from eval.run import RESULTS, load_scifact, scifact_items, scifact_metrics, scifact_tarball


def make_groups(items: list[dict], limit: int, seed: int) -> list[list[dict]]:
    """Items sharing a doc_id, chunked to claims.BATCH, groups of >= 2 only; seeded sample of
    whole groups until >= `limit` claims."""
    by_doc: dict[int, list[dict]] = {}
    for it in sorted(items, key=lambda i: (i["doc_id"], i["claim_id"])):
        by_doc.setdefault(it["doc_id"], []).append(it)
    groups = [
        g[k : k + claims.BATCH] for g in by_doc.values() for k in range(0, len(g), claims.BATCH)
    ]
    groups = [g for g in groups if len(g) >= 2]
    random.Random(seed).shuffle(groups)
    out = []
    for g in groups:
        if sum(map(len, out)) >= limit:
            break
        out.append(g)
    return out


def _inputs(group: list[dict]) -> tuple[list[tuple[Citation, str]], SourceText]:
    ref_id = str(group[0]["doc_id"])
    cits = [Citation(id=f"sf{i['claim_id']}", sentence=i["claim"], ref_ids=[ref_id]) for i in group]
    src = SourceText(
        ref_id=ref_id, kind=group[0]["kind"], text=group[0]["text"], origin="semanticscholar"
    )
    return [(c, ref_id) for c in cits], src


def _row(item: dict, res, error: str | None = None) -> dict:
    """A failed check (res None) is insufficient evidence, never a verdict."""
    return {k: item[k] for k in ("claim_id", "doc_id", "claim", "gold")} | {
        "verdict": res.verdict.value if res else "insufficient_evidence",
        "quote": res.quote if res else None,
        "quote_in_source": bool(res and res.quote) and verify_quote(res.quote, item["text"]),
        "needs_review": res.needs_review if res else True,
        "judge": res.judge.verdict if res and res.judge else None,
        "skeptic": res.skeptic.verdict if res and res.skeptic else None,
        "note": res.note if res else None,
        "error": error,
    }


async def run_mode(groups: list[list[dict]], batched: bool) -> dict:
    t = timing.start()  # shared by the tasks below (each copies the contextvar)
    sem = asyncio.Semaphore(get_settings().llm_concurrency)

    async def single(c, r, src):
        async with sem:
            return await claims.check_claim(c, r, src)

    async def group(g):
        items, src = _inputs(g)
        try:
            if batched:
                async with sem:
                    res = await claims.check_claims(items, src)
            else:
                res = await asyncio.gather(*(single(c, r, src) for c, r in items))
            return [_row(i, r) for i, r in zip(g, res, strict=True)]
        except Exception as e:
            return [_row(i, None, f"{type(e).__name__}: {e}"[:200]) for i in g]

    t0 = time.perf_counter()
    rows = [r for rs in await asyncio.gather(*(group(g) for g in groups)) for r in rs]
    wall = time.perf_counter() - t0
    m = scifact_metrics(rows)
    m |= {
        "judge_calls": t.counts["llm_judge"],
        "skeptic_calls": t.counts["llm_skeptic"],
        "llm_calls": t.counts["llm_judge"] + t.counts["llm_skeptic"],
        "wall_s": round(wall, 1),
    }
    return {"metrics": m, "rows": rows}


async def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m eval.batch_claims")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    # long abstracts go through retrieval: keep the embedding model in the gitignored eval cache
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(RESULTS.parent / ".cache" / "fastembed"))
    os.environ.setdefault("HF_HOME", str(RESULTS.parent / ".cache" / "hf"))
    dev, corpus = load_scifact(scifact_tarball())
    groups = make_groups(scifact_items(dev, corpus, None, args.seed), args.limit, args.seed)
    per = await run_mode(groups, batched=False)
    bat = await run_mode(groups, batched=True)
    same = sum(a["verdict"] == b["verdict"] for a, b in zip(per["rows"], bat["rows"], strict=True))
    res = {
        "suite": "batch_claims",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "metrics": {
            "per_claim": per["metrics"],
            "batched": bat["metrics"],
            "groups": len(groups),
            "verdict_agreement": same / (len(per["rows"]) or 1),
        },
        "rows": {"per_claim": per["rows"], "batched": bat["rows"]},
        "params": {"limit": args.limit, "seed": args.seed},
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "batch_claims.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps(res["metrics"], indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
