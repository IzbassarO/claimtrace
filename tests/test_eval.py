import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `eval` is a top-level folder, not an installed package

from claimtrace.core.schemas import (  # noqa: E402
    ClaimCheck,
    Mismatch,
    ModelOpinion,
    ProviderOutcome,
    ReferenceAudit,
    RefStatus,
    Verdict,
)
from eval import make_refs, run  # noqa: E402

SEEDS = [
    {"doi": "10.1/a", "title": "Deep learning", "authors": ["LeCun, Yann", "Bengio, Y."],
     "year": 2015, "venue": "Nature", "retracted": False},
    {"doi": "10.1/b", "title": "Why most findings are false", "authors": ["Ioannidis, J."],
     "year": 2005, "venue": "PLoS Med", "retracted": False},
    {"doi": "10.1/c", "title": "Molecular structure", "authors": ["Lecun, Y."],
     "year": 1953, "venue": "Nature", "retracted": False},
    {"doi": "10.1/r", "title": "Stimulus-triggered fate conversion", "authors": ["Obokata, H."],
     "year": 2014, "venue": "Nature", "retracted": True},
]  # fmt: skip


def test_negate_title():
    assert run.negate_title("Why most findings are false") == "Why most findings are not false"
    assert run.negate_title("Deep learning") == "No Deep learning"


def test_make_ref_cases_mutations():
    cases = run.make_ref_cases(SEEDS)
    by = {c["id"]: c for c in cases}
    assert len(cases) == 3 * 5 + 1
    assert {c["expected"] for c in cases if c["mutation"] == "unchanged"} == {"verified"}
    assert by["0-unchanged"]["reference"].doi == "10.1/a"
    assert "https://doi.org/10.1/a" in by["0-unchanged"]["reference"].raw
    assert by["0-year_plus_3"]["reference"].year == 2018
    assert by["0-year_plus_3"]["expected"] == "metadata_mismatch"
    assert "(2018)" in by["0-year_plus_3"]["reference"].raw
    neg = by["1-negated_title"]["reference"]
    assert neg.title == "Why most findings are not false" and neg.doi == "10.1/b"
    assert by["0-swapped_first_author"]["reference"].authors == ["Ioannidis, J."]
    # seed 0's first author shares the family name (LeCun/Lecun), so it is skipped
    assert by["2-swapped_first_author"]["reference"].authors == ["Ioannidis, J."]
    fab = by["0-fabricated"]["reference"]
    assert fab.doi is None and by["0-fabricated"]["expected"] == "not_found"
    assert fab.title not in {s["title"] for s in SEEDS}
    assert [c["id"] for c in cases if c["mutation"] == "retracted"] == ["3-retracted"]
    assert by["3-retracted"]["expected"] == "retracted"


def test_make_ref_cases_deterministic():
    a = [c["reference"] for c in run.make_ref_cases(SEEDS, seed=1)]
    assert a == [c["reference"] for c in run.make_ref_cases(SEEDS, seed=1)]
    b = [c["reference"] for c in run.make_ref_cases(SEEDS, seed=2)]
    assert a != b  # only the fabricated references depend on the seed


def test_seed_file_is_real_and_big_enough():
    rows = [json.loads(x) for x in run.SEED_FILE.read_text().splitlines()]
    assert len([r for r in rows if not r["retracted"]]) >= 30
    assert sum(r["retracted"] for r in rows) >= 3
    for r in rows:
        assert r["doi"] and r["title"] and r["authors"] and r["year"]
        assert not r["title"].upper().startswith("RETRACTED")
    assert len(run.make_ref_cases(rows)) > 150


def audit(case, status, mismatches=(), failed=False):
    return ReferenceAudit(
        reference=case["reference"],
        status=status,
        mismatches=[Mismatch(field=f, cited="x", found="y") for f in mismatches],
        providers=[ProviderOutcome(provider="crossref", ok=not failed)],
    )


def test_ref_metrics():
    cases = run.make_ref_cases(SEEDS)
    status = {
        "unchanged": RefStatus.verified,
        "year_plus_3": RefStatus.metadata_mismatch,
        "negated_title": RefStatus.verified,  # matcher missed the negation: false verified
        "swapped_first_author": RefStatus.metadata_mismatch,
        "fabricated": RefStatus.unchecked,  # provider down: honest, but not correct
        "retracted": RefStatus.retracted,
    }
    audits = [audit(c, status[c["mutation"]], failed=c["mutation"] == "fabricated") for c in cases]
    audits[0] = audit(cases[0], RefStatus.not_found)  # a real reference called fake
    m = run.ref_metrics(cases, audits)["metrics"]
    assert m["n"] == 16
    assert m["false_verified"] == 3
    assert m["false_not_found"] == 1
    assert m["unchecked_rate"] == 3 / 16
    assert m["per_mutation"]["negated_title"]["accuracy"] == 0
    assert m["per_mutation"]["unchanged"] == {
        "n": 3, "correct": 2, "accuracy": 2 / 3, "statuses": {"not_found": 1, "verified": 2},
    }  # fmt: skip
    assert m["per_mutation"]["retracted"]["accuracy"] == 1
    assert m["accuracy"] == (2 + 3 + 0 + 3 + 0 + 1) / 16


async def test_run_refs_end_to_end(monkeypatch, tmp_path):
    async def fake_audit(refs, client, on_progress=None):
        return [ReferenceAudit(reference=r, status=RefStatus.verified) for r in refs]

    monkeypatch.setattr(run.refaudit, "audit_references", fake_audit)
    monkeypatch.setattr(run, "RESULTS", tmp_path)
    await run.main(["--suite", "refs", "--limit", "4"])
    res = json.loads((tmp_path / "refs.json").read_text())
    assert res["metrics"]["n"] == 20 and res["metrics"]["false_verified"] == 16
    summary = (tmp_path / "summary.md").read_text()
    assert "## refs" in summary and "| negated_title | 4 | 0.000 |" in summary


def sf_tarball(path: Path) -> Path:
    dev = [
        {"id": 1, "claim": "A causes B.", "evidence": {"10": [{"sentences": [0], "label":
         "SUPPORT"}]}, "cited_doc_ids": [10]},
        {"id": 2, "claim": "C lowers D.", "evidence": {"20": [{"sentences": [1], "label":
         "CONTRADICT"}]}, "cited_doc_ids": [20, 10]},
        {"id": 3, "claim": "E is F.", "evidence": {}, "cited_doc_ids": [30, 99]},
    ]  # fmt: skip
    corpus = [
        {"doc_id": d, "title": f"T{d}", "abstract": [f"Sentence one of {d}.", "Two."]}
        for d in (10, 20, 30)
    ]
    tb = path / "sf.tar.gz"
    with tarfile.open(tb, "w:gz") as tar:
        for name, rows in [("claims_dev.jsonl", dev), ("corpus.jsonl", corpus)]:
            data = "".join(json.dumps(r) + "\n" for r in rows).encode()
            info = tarfile.TarInfo(f"data/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return tb


def test_scifact_items(tmp_path):
    dev, corpus = run.load_scifact(sf_tarball(tmp_path))
    items = run.scifact_items(dev, corpus, 100, 0)
    gold = {(i["claim_id"], i["doc_id"]): i["gold"] for i in items}
    # doc 99 is missing from the corpus and is skipped
    assert gold == {
        (1, 10): "supported", (2, 20): "unsupported", (2, 10): "nei", (3, 30): "nei",
    }  # fmt: skip
    assert next(i for i in items if i["doc_id"] == 30)["text"] == "T30\n\nSentence one of 30. Two."
    assert run.scifact_items(dev, corpus, 2, 7) == run.scifact_items(dev, corpus, 2, 7)
    assert len(run.scifact_items(dev, corpus, 2, 7)) == 2


def row(gold, verdict, quote="q", in_src=True, review=False, error=None):
    return {"gold": gold, "verdict": verdict, "quote": quote, "quote_in_source": in_src,
            "needs_review": review, "error": error}  # fmt: skip


def test_scifact_metrics():
    rows = [
        row("supported", "supported"),
        row("supported", "insufficient_evidence", quote=None, in_src=False, review=True),
        row("unsupported", "unsupported"),
        row("unsupported", "partly_supported"),  # false supported
        row("nei", "insufficient_evidence", quote=None, in_src=False),
        row("nei", "supported", in_src=False),  # false supported + quote gate violation
        row("nei", "unsupported", error=None),
        row("supported", "insufficient_evidence", quote=None, in_src=False, error="boom"),
    ]
    m = run.scifact_metrics(rows)
    assert m["n"] == 8
    assert m["accuracy"] == 4 / 8  # rows 0, 2, 4, 6
    assert m["supported_precision"] == 1 / 3
    assert m["supported_recall"] == 1 / 3
    assert m["false_supported_rate"] == 2 / 5
    assert m["quote_verified_rate"] == 5 / 8
    assert m["quote_gate_violations"] == 1
    assert m["needs_review_rate"] == 1 / 8
    assert m["errors"] == 1
    assert m["confusion"]["nei"] == {"insufficient_evidence": 1, "supported": 1, "unsupported": 1}


ITEM = {"claim_id": 5, "doc_id": 7, "claim": "X raises Y.", "gold": "supported",
        "text": "Title\n\nWe found that X  raises Y in all  tested mice."}  # fmt: skip


async def test_check_item_failure_is_insufficient_evidence(monkeypatch):
    async def boom(citation, ref_id, source):
        raise NotImplementedError

    monkeypatch.setattr(run.claims, "check_claim", boom)
    r = await run.check_item(ITEM)
    assert r["verdict"] == "insufficient_evidence" and r["error"].startswith("NotImplementedError")
    assert r["needs_review"] and r["quote"] is None


async def test_check_item_passes_source_and_checks_quote(monkeypatch):
    seen = {}

    async def fake(citation, ref_id, source):
        seen.update(sentence=citation.sentence, ref_id=ref_id, text=source.text)
        return ClaimCheck(
            citation_id=citation.id,
            ref_id=ref_id,
            sentence=citation.sentence,
            verdict=Verdict.supported,
            quote="found that X raises Y in all tested",
            judge=ModelOpinion(verdict="supported"),
        )

    monkeypatch.setattr(run.claims, "check_claim", fake)
    r = await run.check_item(ITEM)
    assert seen == {"sentence": "X raises Y.", "ref_id": "7", "text": ITEM["text"]}
    assert r["verdict"] == "supported" and r["quote_in_source"] and r["judge"] == "supported"
    assert r["error"] is None


def test_make_refs_parse_crossref():
    msg = {
        "DOI": "10.1038/NATURE12968",
        "title": ["RETRACTED ARTICLE: Stimulus-triggered <i>fate</i>  conversion"],
        "author": [{"family": "Obokata", "given": "Haruko"}, {"name": "Some Consortium"}],
        "issued": {"date-parts": [[2014, 1, 29]]},
        "container-title": ["Nature"],
        "updated-by": [{"type": "retraction", "DOI": "10.1038/nature13598"}],
    }
    assert make_refs.parse_crossref(msg) == {
        "doi": "10.1038/nature12968",
        "title": "Stimulus-triggered fate conversion",
        "authors": ["Obokata, Haruko", "Some Consortium"],
        "year": 2014,
        "venue": "Nature",
    }
    assert make_refs.crossref_retracted(msg)
    assert not make_refs.crossref_retracted({})
