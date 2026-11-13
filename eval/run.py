"""Evaluation harness.

    uv run python -m eval.run --suite scifact|refs|all [--limit N] [--seed S]

Writes eval/results/<suite>.json and eval/results/summary.md (from every results file present).

- scifact: SciFact dev claims vs. their cited abstracts through `claims.check_claim`.
- refs: real references from eval/data/refs_seed.jsonl with deterministic injected errors
  through `refaudit.audit_references`.
"""

import argparse
import asyncio
import json
import random
import re
import tarfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from claimtrace import claims, refaudit
from claimtrace.core.http import make_client
from claimtrace.core.schemas import Citation, EvidenceKind, Reference, SourceText
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


def make_ref_cases(seeds: list[dict], seed: int = 0) -> list[dict]:
    """Seed rows -> [{id, mutation, expected, reference}]. Pure and deterministic."""
    cases = []
    normal = [s for s in seeds if not s.get("retracted")]

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
            j = normal.index(s)
            others = normal[j + 1 :] + normal[:j]
            other = next(
                o["authors"][0]
                for o in others
                if o["authors"] and family(o["authors"][0]) != family(s["authors"][0])
            )
            add(i, "swapped_first_author", "metadata_mismatch", base | {"authors": [other]})
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


async def run_refs(limit: int | None, seed: int) -> dict:
    seeds = [json.loads(line) for line in SEED_FILE.read_text().splitlines() if line.strip()]
    cases = make_ref_cases(seeds[:limit] if limit else seeds, seed)
    async with make_client() as client:
        audits = await refaudit.audit_references([c["reference"] for c in cases], client)
    return ref_metrics(cases, audits)


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


def scifact_items(dev: list[dict], corpus: dict, n: int, seed: int) -> list[dict]:
    """One item per (claim, cited abstract); gold from the evidence for that abstract."""
    labels = {"SUPPORT": "supported", "CONTRADICT": "unsupported"}
    items = []
    for c in dev:
        for doc_id in c.get("cited_doc_ids", []):
            doc = corpus.get(doc_id)
            if not doc:
                continue
            ev = c.get("evidence", {}).get(str(doc_id))
            items.append(
                {
                    "claim_id": c["id"],
                    "doc_id": doc_id,
                    "claim": c["claim"],
                    "gold": labels[ev[0]["label"]] if ev else "nei",
                    "text": doc["title"] + "\n\n" + " ".join(doc["abstract"]),
                }
            )
    return random.Random(seed).sample(items, min(n, len(items)))


def norm(s: str) -> str:
    s = s.replace("­", "").replace("​", "")
    s = s.translate(str.maketrans("‘’“”", "''\"\""))
    return " ".join(s.split()).casefold()


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
    src = SourceText(
        ref_id=ref_id, kind=EvidenceKind.abstract, text=item["text"], origin="semanticscholar"
    )
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
            "skeptic": None,
            "error": f"{type(e).__name__}: {e}"[:200],
        }
    return row | {
        "verdict": res.verdict.value,
        "quote": res.quote,
        "quote_in_source": bool(res.quote) and norm(res.quote) in norm(item["text"]),
        "needs_review": res.needs_review,
        "judge": res.judge.verdict if res.judge else None,
        "skeptic": res.skeptic.verdict if res.skeptic else None,
        "error": None,
    }


async def run_scifact(limit: int | None, seed: int) -> dict:
    dev, corpus = load_scifact(scifact_tarball())
    items = scifact_items(dev, corpus, limit or 100, seed)
    sem = asyncio.Semaphore(get_settings().llm_concurrency)

    async def one(item):
        async with sem:
            return await check_item(item)

    rows = list(await asyncio.gather(*(one(i) for i in items)))
    return {"metrics": scifact_metrics(rows), "rows": rows}


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
        if "confusion" in m:
            out += ["| gold \\ verdict | counts |", "|---|---|"]
            for g, d in m["confusion"].items():
                out.append(f"| {g} | {', '.join(f'{k} {v}' for k, v in sorted(d.items()))} |")
            out.append("")
    return "\n".join(out)


def write_results(suite: str, res: dict, params: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    res = {"suite": suite, "created": datetime.now(UTC).isoformat(timespec="seconds")} | res
    res["params"] = params
    (RESULTS / f"{suite}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    all_res = {p.stem: json.loads(p.read_text()) for p in sorted(RESULTS.glob("*.json"))}
    (RESULTS / "summary.md").write_text(render_summary(all_res))


async def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m eval.run")
    ap.add_argument("--suite", choices=["scifact", "refs", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="scifact: sample size (default 100)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    runners = {"scifact": run_scifact, "refs": run_refs}
    for suite in runners if args.suite == "all" else [args.suite]:
        res = await runners[suite](args.limit, args.seed)
        write_results(suite, res, {"limit": args.limit, "seed": args.seed})
        print(f"{suite}: " + json.dumps(res["metrics"], default=str))
    print(f"summary: {RESULTS / 'summary.md'}")


if __name__ == "__main__":
    asyncio.run(main())
