"""Evaluation harness.

    uv run python -m eval.run --suite SUITE [--limit N] [--seed S]

Writes eval/results/<suite>.json, eval/results/summary.md and eval/results/ablation.md (both
rebuilt from every results file present). See eval/README.md.

- scifact: SciFact dev claims vs. their cited abstracts through `claims.check_claim` (LLM).
- scifact-haystack: the same, but the source is a haystack (LLM).
- retrieval: gold rationale sentences vs. `claims.retrieval.retrieve` on haystacks (no LLM).
- refs: real references from eval/data/refs_seed.jsonl with deterministic injected errors
  through `refaudit.audit_references`.
"""

import argparse
import asyncio
import json
import os
import random
import re
import tarfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from claimtrace import claims, refaudit
from claimtrace.claims import retrieval
from claimtrace.claims.quote import verify_quote
from claimtrace.core.http import make_client
from claimtrace.core.schemas import Citation, EvidenceKind, ModelOpinion, Reference, SourceText
from claimtrace.core.settings import get_settings

HERE = Path(__file__).parent
CACHE = HERE / ".cache"
RESULTS = HERE / "results"
SEED_FILE = HERE / "data" / "refs_seed.jsonl"
SCIFACT_URL = "https://scifact.s3-us-west-2.amazonaws.com/release/latest/data.tar.gz"

POSITIVE = {"supported", "partly_supported"}  # verdicts that tell the user "the source agrees"
WITH_QUOTE = POSITIVE | {"unsupported"}  # verdicts that must carry a verbatim quote


# ---------------------------------------------------------------- refs: injected errors


def family(author: str) -> str:
    return (author.split(",")[0] if "," in author else author.split(" ")[-1]).strip().casefold()


def negate_title(title: str) -> str:
    m = re.search(r"\b(is|are|was|were|does|do)\b", title, re.I)
    if m:
        return f"{title[: m.end()]} not{title[m.end() :]}"
    return f"No {title}"


FAKE_A = ["Quantum", "Stochastic", "Hierarchical", "Allosteric", "Recursive", "Topological"]
FAKE_B = ["entanglement", "modulation", "remodelling", "calibration", "inhibition", "folding"]
FAKE_C = ["migratory songbird", "zebrafish retinal", "lunar regolith", "cortical astrocyte"]
FAKE_D = ["circadian proteins", "glacier microbiomes", "byzantine ledgers", "tidal turbines"]
FAKE_NAMES = ["Varkonyi", "Lindqvist", "Okonkwo-Brandt", "Tremaine", "Haddadi", "Rosenquist"]


def fabricate(rng: random.Random) -> dict:
    title = (
        f"{rng.choice(FAKE_A)} {rng.choice(FAKE_B)} of {rng.choice(FAKE_C)} "
        f"{rng.choice(FAKE_D)} under {rng.choice(FAKE_C)} conditions"
    )
    authors = [f"{n}, {rng.choice('ABCDEHJKMR')}." for n in rng.sample(FAKE_NAMES, 3)]
    return {
        "title": title,
        "authors": authors,
        "year": rng.randint(2001, 2022),
        "venue": "Journal of Interdisciplinary Applied Studies",
        "doi": None,
    }


def make_raw(r: dict) -> str:
    parts = ["; ".join(r["authors"]) + f" ({r['year']}).", r["title"] + "."]
    if r.get("venue"):
        parts.append(r["venue"] + ".")
    if r.get("doi"):
        parts.append(f"https://doi.org/{r['doi']}")
    return " ".join(parts)


FABRICATED = 150  # a fabricated reference costs one paid OpenAlex search; the others are free


def make_ref_cases(seeds: list[dict], seed: int = 0, fabricated: int = FABRICATED) -> list[dict]:
    """Seed rows -> [{id, mutation, expected, reference}]. Pure and deterministic.
    Only a seeded subset of `fabricated` seeds gets the `fabricated` mutation (budget)."""
    cases = []
    normal = [s for s in seeds if not s.get("retracted")]
    fab = set(random.Random(seed).sample(range(len(normal)), min(fabricated, len(normal))))

    def add(i: int, mutation: str, expected: str, r: dict):
        ref = Reference(
            id=f"{i}-{mutation}",
            raw=make_raw(r),
            title=r["title"],
            authors=r["authors"],
            year=r["year"],
            doi=r["doi"],
            venue=r.get("venue"),
        )
        cases.append({"id": ref.id, "mutation": mutation, "expected": expected, "reference": ref})

    pos = {id(s): j for j, s in enumerate(normal)}
    for i, s in enumerate(seeds):
        base = {k: s.get(k) for k in ("title", "authors", "year", "venue", "doi")}
        if s.get("retracted"):
            add(i, "retracted", "retracted", base)
            continue
        add(i, "unchanged", "verified", base)
        add(i, "year_plus_3", "metadata_mismatch", base | {"year": s["year"] + 3})
        add(i, "negated_title", "metadata_mismatch", base | {"title": negate_title(s["title"])})
        if s["authors"]:
            # first author of the next seed whose first author has a different family name
            j = pos[id(s)]
            others = normal[j + 1 :] + normal[:j]
            other = next(
                o["authors"][0]
                for o in others
                if o["authors"] and family(o["authors"][0]) != family(s["authors"][0])
            )
            add(i, "swapped_first_author", "metadata_mismatch", base | {"authors": [other]})
        if pos[id(s)] in fab:
            add(i, "fabricated", "not_found", fabricate(random.Random(seed * 100_003 + i)))
    return cases


def ref_metrics(cases: list[dict], audits: list) -> dict:
    rows = [
        {
            "id": c["id"],
            "mutation": c["mutation"],
            "expected": c["expected"],
            "status": a.status.value,
            "mismatches": [m.field for m in a.mismatches],
            "providers_failed": [p.provider for p in a.providers if not p.ok],
        }
        for c, a in zip(cases, audits, strict=True)
    ]
    per_mutation = {}
    for m in dict.fromkeys(r["mutation"] for r in rows):
        sub = [r for r in rows if r["mutation"] == m]
        ok = sum(r["status"] == r["expected"] for r in sub)
        per_mutation[m] = {
            "n": len(sub),
            "correct": ok,
            "accuracy": ok / len(sub),
            "statuses": dict(Counter(r["status"] for r in sub)),
        }
    n = len(rows) or 1
    metrics = {
        "n": len(rows),
        "accuracy": sum(r["status"] == r["expected"] for r in rows) / n,
        # a wrong reference reported as fine: the error this product must not make
        "false_verified": sum(r["status"] == "verified" != r["expected"] for r in rows),
        # a real reference reported as non-existent
        "false_not_found": sum(r["status"] == "not_found" != r["expected"] for r in rows),
        "unchecked_rate": sum(r["status"] == "unchecked" for r in rows) / n,
        "per_mutation": per_mutation,
    }
    return {"metrics": metrics, "rows": rows}


def searches_today() -> int:
    """Paid OpenAlex searches counted today (UTC) by core.http's on-disk counter."""
    day = Path(get_settings().cache_dir) / "openalex" / f"searches-{datetime.now(UTC):%Y-%m-%d}.txt"
    try:
        return int(day.read_text() or 0)
    except (OSError, ValueError):
        return 0


async def run_refs(limit: int | None, seed: int) -> dict:
    seeds = [json.loads(line) for line in SEED_FILE.read_text().splitlines() if line.strip()]
    cases = make_ref_cases(seeds[:limit] if limit else seeds, seed)
    before = searches_today()
    async with make_client() as client:
        audits = await refaudit.audit_references([c["reference"] for c in cases], client)
    res = ref_metrics(cases, audits)
    res["metrics"]["openalex_searches"] = searches_today() - before
    print(
        f"refs: OpenAlex searches today {before} -> {before + res['metrics']['openalex_searches']}"
    )
    return res


# ---------------------------------------------------------------- scifact


def scifact_tarball() -> Path:
    path = CACHE / "scifact.tar.gz"
    if not path.exists():
        import httpx

        CACHE.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", SCIFACT_URL, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(path.with_suffix(".part"), "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        path.with_suffix(".part").rename(path)
    return path


def load_scifact(tarball: Path) -> tuple[list[dict], dict[int, dict]]:
    """Read dev claims and corpus straight from the tarball (no extraction to disk)."""
    with tarfile.open(tarball) as tar:

        def jsonl(name: str) -> list[dict]:
            f = tar.extractfile(f"data/{name}")
            return [json.loads(line) for line in f.read().decode().splitlines() if line.strip()]

        dev = jsonl("claims_dev.jsonl")
        corpus = {d["doc_id"]: d for d in jsonl("corpus.jsonl")}
    return dev, corpus


DISTRACTORS = 20


def make_haystack(corpus: dict, doc_id: int, exclude: set, rng: random.Random) -> str:
    """The cited abstract among DISTRACTORS other abstracts (none cited by the claim), at a
    random position, one paragraph each. Long enough that retrieval is actually used."""
    pool = sorted(d for d in corpus if d not in exclude and d != doc_id)
    docs = rng.sample(pool, min(DISTRACTORS, len(pool)))
    docs.insert(rng.randrange(len(docs) + 1), doc_id)
    return "\n\n".join(" ".join(corpus[d]["abstract"]) for d in docs)


def scifact_items(
    dev: list[dict], corpus: dict, n: int | None, seed: int, haystack: bool = False
) -> list[dict]:
    """One item per (claim, cited abstract); gold from the evidence for that abstract.
    `rationale` = gold evidence sentences of that abstract (empty for NEI)."""
    labels = {"SUPPORT": "supported", "CONTRADICT": "unsupported"}
    items = []
    for c in dev:
        for doc_id in c.get("cited_doc_ids", []):
            doc = corpus.get(doc_id)
            if not doc:
                continue
            ev = c.get("evidence", {}).get(str(doc_id))
            idx = sorted({i for e in ev or [] for i in e["sentences"]})
            if haystack:
                rng = random.Random(f"{seed}:{c['id']}:{doc_id}")
                text = make_haystack(corpus, doc_id, set(c["cited_doc_ids"]), rng)
            else:
                text = doc["title"] + "\n\n" + " ".join(doc["abstract"])
            items.append(
                {
                    "claim_id": c["id"],
                    "doc_id": doc_id,
                    "claim": c["claim"],
                    "gold": labels[ev[0]["label"]] if ev else "nei",
                    "rationale": [doc["abstract"][i] for i in idx],
                    "kind": EvidenceKind.full_text if haystack else EvidenceKind.abstract,
                    "text": text,
                }
            )
    return random.Random(seed).sample(items, min(n or len(items), len(items)))


def predicted(verdict: str) -> str:
    return "supported" if verdict in POSITIVE else verdict if verdict == "unsupported" else "nei"


def scifact_metrics(rows: list[dict]) -> dict:
    n = len(rows) or 1

    def correct(r):
        p = predicted(r["verdict"])
        return p != "supported" if r["gold"] == "nei" else p == r["gold"]

    pos = [r for r in rows if r["verdict"] in POSITIVE]
    gold_pos = [r for r in rows if r["gold"] == "supported"]
    tp = sum(r["gold"] == "supported" for r in pos)
    neg = [r for r in rows if r["gold"] != "supported"]
    confusion = {}
    for r in rows:
        confusion.setdefault(r["gold"], Counter())[r["verdict"]] += 1
    return {
        "n": len(rows),
        "gold": dict(Counter(r["gold"] for r in rows)),
        "accuracy": sum(map(correct, rows)) / n,
        "supported_precision": tp / len(pos) if pos else None,
        "supported_recall": tp / len(gold_pos) if gold_pos else None,
        # CONTRADICT or NEI claims we told the user were supported: the worst error
        "false_supported_rate": sum(r["verdict"] in POSITIVE for r in neg) / len(neg)
        if neg
        else None,
        "quote_verified_rate": sum(r["quote"] is not None for r in rows) / n,
        # verdicts that need a quote but lack one, or whose quote is not in the source
        "quote_gate_violations": sum(
            r["verdict"] in WITH_QUOTE and not r["quote_in_source"] for r in rows
        ),
        "needs_review_rate": sum(r["needs_review"] for r in rows) / n,
        "errors": sum(r["error"] is not None for r in rows),
        "confusion": {g: dict(c) for g, c in confusion.items()},
    }


async def check_item(item: dict) -> dict:
    ref_id = str(item["doc_id"])
    cit = Citation(id=f"sf{item['claim_id']}", sentence=item["claim"], ref_ids=[ref_id])
    # SciFact abstracts come from S2ORC (Semantic Scholar), hence the origin
    src = SourceText(ref_id=ref_id, kind=item["kind"], text=item["text"], origin="semanticscholar")
    row = {k: item[k] for k in ("claim_id", "doc_id", "claim", "gold")}
    try:
        res = await claims.check_claim(cit, ref_id, src)
    except Exception as e:  # a failed check is insufficient evidence, never a verdict
        return row | {
            "verdict": "insufficient_evidence",
            "quote": None,
            "quote_in_source": False,
            "needs_review": True,
            "judge": None,
            "judge_quote": None,
            "skeptic": None,
            "skeptic_quote": None,
            "error": f"{type(e).__name__}: {e}"[:200],
        }
    return row | {
        "verdict": res.verdict.value,
        "quote": res.quote,
        # the product's own strict gate (word boundaries, digits, >= 5 words), not a substring
        "quote_in_source": bool(res.quote) and verify_quote(res.quote, item["text"]),
        "needs_review": res.needs_review,
        # both opinions with their quotes: the ablation recomputes verdicts from them post hoc
        "judge": res.judge.verdict if res.judge else None,
        "judge_quote": res.judge.quote if res.judge else None,
        "skeptic": res.skeptic.verdict if res.skeptic else None,
        "skeptic_quote": res.skeptic.quote if res.skeptic else None,
        "error": None,
    }


def _opinion(row: dict, role: str) -> ModelOpinion | None:
    return ModelOpinion(verdict=row[role], quote=row[f"{role}_quote"] or "") if row[role] else None


def _cautious(*ops: ModelOpinion | None) -> str:
    return max(
        (o.verdict for o in ops if o), key=claims.CAUTION.index, default="insufficient_evidence"
    )


# variant -> (judge, skeptic, source text) -> verdict. The last one is the product (claims.combine).
ABLATIONS = {
    "judge only (no quote gate)": lambda j, s, text: _cautious(j),
    "judge + skeptic (no gate)": lambda j, s, text: _cautious(j, s),
    "judge + quote gate": lambda j, s, text: claims.combine(j, None, text)[0],
    "judge + skeptic + quote gate": lambda j, s, text: claims.combine(j, s, text)[0],
}
ABLATION_KEYS = ("n", "accuracy", "false_supported_rate", "supported_precision", "supported_recall")


def ablation(rows: list[dict], texts: list[str]) -> dict:
    """Verdicts each pipeline variant would have given, from the judge/skeptic answers stored in
    the rows. No LLM calls. `product_mismatches` should be 0: the full variant is the product."""
    out = {}
    for name, verdict_of in ABLATIONS.items():
        alt = [
            r | {"verdict": verdict_of(_opinion(r, "judge"), _opinion(r, "skeptic"), t)}
            for r, t in zip(rows, texts, strict=True)
        ]
        m = scifact_metrics(alt)
        out[name] = {k: m[k] for k in ABLATION_KEYS}
    full = ABLATIONS["judge + skeptic + quote gate"]
    out["product_mismatches"] = sum(
        r["error"] is None and full(_opinion(r, "judge"), _opinion(r, "skeptic"), t) != r["verdict"]
        for r, t in zip(rows, texts, strict=True)
    )
    return out


async def run_scifact(limit: int | None, seed: int, haystack: bool = False) -> dict:
    dev, corpus = load_scifact(scifact_tarball())
    items = scifact_items(dev, corpus, limit or 100, seed, haystack)
    sem = asyncio.Semaphore(get_settings().llm_concurrency)

    async def one(item):
        async with sem:
            return await check_item(item)

    rows = list(await asyncio.gather(*(one(i) for i in items)))
    abl = ablation(rows, [i["text"] for i in items])
    return {"metrics": scifact_metrics(rows), "ablation": abl, "rows": rows}


async def run_scifact_haystack(limit: int | None, seed: int) -> dict:
    return await run_scifact(limit, seed, haystack=True)


# ---------------------------------------------------------------- retrieval (no LLM)


def _no_embed(texts: list[str]):
    raise RuntimeError("BM25-only variant")  # retrieve() falls back to BM25 on any failure


_embed_memo: dict[tuple, list] = {}
_embed_errors: list[str] = []


def _memo_embed(texts: list[str]):
    """fastembed, memoised per item (the rank probe re-runs retrieve with the same candidates).
    Failures are recorded: retrieve() swallows them, so a broken embedder would look like BM25."""
    key = tuple(texts)
    if key not in _embed_memo:
        try:
            _embed_memo[key] = retrieval.fastembed_embed(texts)
        except Exception as e:
            _embed_errors.append(f"{type(e).__name__}: {e}"[:200])
            raise
    return _embed_memo[key]


def _no_rerank(query: str, docs: list[str]):
    raise RuntimeError("no cross-encoder")  # retrieve() keeps the fused order on any failure


_rerank_memo: dict[tuple, list] = {}


def _memo_rerank(query: str, docs: list[str]):
    """Cross-encoder, memoised (the rank probe re-runs retrieve on the same pool). Failures are
    counted with the embedder's: retrieve() swallows them (a broken model looks like hybrid)."""
    key = (query, *docs)
    if key not in _rerank_memo:
        try:
            _rerank_memo[key] = retrieval.fastembed_rerank(query, docs)
        except Exception as e:
            _embed_errors.append(f"rerank {type(e).__name__}: {e}"[:200])
            raise
    return _rerank_memo[key]


# name -> async (query, text) -> passages. Add a variant here to measure it.
RETRIEVAL_VARIANTS = {
    "bm25": lambda q, t: retrieval.retrieve(q, t, embed=_no_embed, rerank=_no_rerank),
    "hybrid": lambda q, t: retrieval.retrieve(q, t, embed=_memo_embed, rerank=_no_rerank),
    "rerank": lambda q, t: retrieval.retrieve(q, t, embed=_memo_embed, rerank=_memo_rerank),
}


def _norm(s: str) -> str:
    return " ".join(s.split())


def has_gold(passages: list[str], gold: list[str]) -> bool:
    """At least one gold sentence fully inside one returned passage (whitespace-normalised)."""
    return any(_norm(g) in _norm(p) for g in gold for p in passages)


async def first_gold_rank(variant, query: str, text: str, gold: list[str]) -> int | None:
    """1-based rank of the first passage with gold evidence. retrieve() returns its top passages
    in document order, so the rank is the smallest k whose top-k (retrieval.TOP_K = k) has one.
    ponytail: patches a module global, so items run one at a time."""
    top_k = retrieval.TOP_K
    try:
        for k in range(1, top_k + 1):
            retrieval.TOP_K = k
            if has_gold(await variant(query, text), gold):
                return k
    finally:
        retrieval.TOP_K = top_k
    return None


def retrieval_metrics(rows: list[dict]) -> dict:
    n = len(rows) or 1
    variants = {}
    for name in rows[0]["variants"] if rows else []:
        vs = [r["variants"][name] for r in rows]
        variants[name] = {
            f"recall@{retrieval.TOP_K}": sum(v["hit"] for v in vs) / n,
            "mrr": sum(1 / v["rank"] for v in vs if v["rank"]) / n,
            "mean_latency_ms": sum(v["ms"] for v in vs) / n,
        }
    return {
        "n": len(rows),
        "haystack_chars_min": min((r["chars"] for r in rows), default=0),
        "embed_errors": len(_embed_errors),
        "variants": variants,
    }


async def run_retrieval(limit: int | None, seed: int) -> dict:
    # the embedding model downloads into the gitignored eval cache, not the system temp dir
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(CACHE / "fastembed"))
    os.environ.setdefault("HF_HOME", str(CACHE / "hf"))
    dev, corpus = load_scifact(scifact_tarball())
    items = [i for i in scifact_items(dev, corpus, None, seed, haystack=True) if i["rationale"]]
    items = items[:limit] if limit else items
    _embed_errors.clear()
    for variant in RETRIEVAL_VARIANTS.values():  # warm-up (model load) is not timed
        await variant(items[0]["claim"], items[0]["text"])
    rows = []
    for it in items:
        _embed_memo.clear()
        _rerank_memo.clear()
        row = {"claim_id": it["claim_id"], "doc_id": it["doc_id"], "chars": len(it["text"])}
        row["variants"] = {}
        for name, variant in RETRIEVAL_VARIANTS.items():
            t0 = time.perf_counter()
            got = await variant(it["claim"], it["text"])
            ms = (time.perf_counter() - t0) * 1000
            hit = has_gold(got, it["rationale"])
            rank = await first_gold_rank(variant, it["claim"], it["text"], it["rationale"])
            row["variants"][name] = {"hit": hit, "rank": rank if hit else None, "ms": ms}
        rows.append(row)
    return {"metrics": retrieval_metrics(rows), "rows": rows}


# ---------------------------------------------------------------- report


def fmt(v) -> str:
    return f"{v:.3f}" if isinstance(v, float) else "n/a" if v is None else str(v)


def render_summary(results: dict[str, dict]) -> str:
    out = ["# ClaimTrace eval summary", ""]
    for suite, res in results.items():
        m = res["metrics"]
        out += [f"## {suite}", "", f"{res['created']} · params `{json.dumps(res['params'])}`", ""]
        out += ["| metric | value |", "|---|---|"]
        out += [f"| {k} | {fmt(v)} |" for k, v in m.items() if not isinstance(v, dict)]
        out.append("")
        if "per_mutation" in m:
            out += ["| mutation | n | accuracy | statuses |", "|---|---|---|---|"]
            for mut, d in m["per_mutation"].items():
                st = ", ".join(f"{k} {v}" for k, v in sorted(d["statuses"].items()))
                out.append(f"| {mut} | {d['n']} | {fmt(d['accuracy'])} | {st} |")
            out.append("")
        if "variants" in m:
            names = list(next(iter(m["variants"].values()), {}))
            out += ["| variant | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
            for v, d in m["variants"].items():
                out.append(f"| {v} | " + " | ".join(fmt(d[k]) for k in names) + " |")
            out.append("")
        if "confusion" in m:
            out += ["| gold \\ verdict | counts |", "|---|---|"]
            for g, d in m["confusion"].items():
                out.append(f"| {g} | {', '.join(f'{k} {v}' for k, v in sorted(d.items()))} |")
            out.append("")
    return "\n".join(out)


def render_ablation(results: dict[str, dict]) -> str:
    out = ["# ClaimTrace verdict ablation", ""]
    out += ["Recomputed from the judge's and skeptic's stored answers (no extra LLM calls).", ""]
    for suite, res in results.items():
        if "ablation" not in res:
            continue
        a = dict(res["ablation"])
        mism = a.pop("product_mismatches")
        out += [f"## {suite}", "", f"{res['created']} · params `{json.dumps(res['params'])}`", ""]
        out += ["| variant | " + " | ".join(ABLATION_KEYS) + " |", "|---" * 6 + "|"]
        out += [
            f"| {v} | " + " | ".join(fmt(d[k]) for k in ABLATION_KEYS) + " |" for v, d in a.items()
        ]
        out += ["", f"product_mismatches (should be 0): {mism}", ""]
    return "\n".join(out)


def write_results(suite: str, res: dict, params: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    res = {"suite": suite, "created": datetime.now(UTC).isoformat(timespec="seconds")} | res
    res["params"] = params
    (RESULTS / f"{suite}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    all_res = {p.stem: json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))}
    (RESULTS / "summary.md").write_text(render_summary(all_res))
    if any("ablation" in r for r in all_res.values()):
        (RESULTS / "ablation.md").write_text(render_ablation(all_res))


async def main(argv: list[str] | None = None) -> None:
    # One eval run may spend at most 250 paid OpenAlex searches (budget rule; cache = free reruns)
    os.environ.setdefault("OPENALEX_RUN_SEARCHES", "250")
    get_settings.cache_clear()
    ap = argparse.ArgumentParser(prog="python -m eval.run")
    runners = {
        "scifact": run_scifact,
        "scifact-haystack": run_scifact_haystack,
        "retrieval": run_retrieval,
        "refs": run_refs,
    }
    ap.add_argument("--suite", choices=[*runners, "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="scifact*: sample size (default 100)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    for suite in ["scifact", "refs"] if args.suite == "all" else [args.suite]:
        res = await runners[suite](args.limit, args.seed)
        write_results(suite, res, {"limit": args.limit, "seed": args.seed})
        print(f"{suite}: " + json.dumps(res["metrics"], default=str))
    print(f"summary: {RESULTS / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main())
