"""Streaming conveyor: partial reports, no stage barriers, problem-first queue, verdict cache."""

import asyncio

import pytest

from claimtrace import claims, ingest, pipeline, refaudit, sources
from claimtrace.core.schemas import (
    Citation,
    ClaimCheck,
    EvidenceKind,
    Manuscript,
    ModelOpinion,
    Reference,
    ReferenceAudit,
    RefStatus,
    SourceText,
    Verdict,
    Work,
)

REFS = [Reference(id=str(i), raw=f"r{i}", title=f"Paper {i}", year=2020) for i in (1, 2, 3)]
CITS = [
    Citation(id=f"c{i}", sentence=f"Claim {i} [{i}].", ref_ids=[str(i)], marker=f"[{i}]")
    for i in (1, 2, 3)
]
OP = ModelOpinion(verdict="supported", quote="a b c d e", rationale="r")


@pytest.fixture
def wired(monkeypatch):
    """Ref 1 audits fast; ref 2 slowly; ref 3 is a metadata mismatch (a 'problem')."""
    log: list[str] = []
    delays = {"1": 0.0, "2": 0.3, "3": 0.05}
    status = {"1": RefStatus.verified, "2": RefStatus.verified, "3": RefStatus.metadata_mismatch}

    async def parse_inputs(text, files):
        return Manuscript(body=text, references=list(REFS), citations=list(CITS))

    async def audit_references(refs, client, on_progress=None, on_result=None, priority=None):
        async def one(i, r):
            await asyncio.sleep(delays[r.id])
            a = ReferenceAudit(
                reference=r, status=status[r.id], matched=Work(provider="crossref", title=r.title)
            )
            log.append(f"audit {r.id}")
            if on_result:
                on_result(i, a)
            return a

        return list(await asyncio.gather(*(one(i, r) for i, r in enumerate(refs))))

    async def fetch_source(audit, client, pdf_to_text):
        rid = audit.reference.id
        return SourceText(ref_id=rid, kind=EvidenceKind.abstract, text=f"t{rid}", origin="openalex")

    calls: list[str] = []

    async def check_claim(c, ref_id, source, **kw):
        calls.append(ref_id)
        log.append(f"claim {ref_id}")
        return ClaimCheck(
            citation_id=c.id,
            ref_id=ref_id,
            sentence=c.sentence,
            verdict=Verdict.supported,
            quote="a b c d e",
            judge=OP,
            skeptic=OP,
        )

    monkeypatch.setattr(ingest, "parse_inputs", parse_inputs)
    monkeypatch.setattr(refaudit, "audit_references", audit_references)
    monkeypatch.setattr(sources, "fetch_source", fetch_source)
    monkeypatch.setattr(claims, "check_claim", check_claim)
    return log, calls


async def test_partials_stream_and_final_report_is_complete_and_ordered(wired):
    log, _ = wired
    snaps = []
    final = await pipeline.run("text", [], on_partial=snaps.append)
    assert snaps and all(not s.complete for s in snaps) and final.complete
    # references are visible before all claims are done
    first_with_ref = next(s for s in snaps if s.references)
    assert len(first_with_ref.claims) < 3
    # final report keeps input order whatever the completion order
    assert [a.reference.id for a in final.references] == ["1", "2", "3"]
    assert [c.citation_id for c in final.claims] == ["c1", "c2", "c3"]
    assert final.summary == {
        "references": {"verified": 2, "metadata_mismatch": 1},
        "claims": {"supported": 3},
    }


async def test_no_stage_barrier_claim_checked_before_slow_audit_finishes(wired):
    log, _ = wired
    await pipeline.run("text", [])
    assert log.index("claim 1") < log.index("audit 2")


async def test_problem_references_are_checked_first(wired, monkeypatch):
    log, calls = wired
    from claimtrace.core import settings as st

    monkeypatch.setenv("LLM_CONCURRENCY", "1")
    st.get_settings.cache_clear()
    gate = asyncio.Event()
    orig = claims.check_claim

    async def slow_first(c, ref_id, source, **kw):  # hold the single worker until all are queued
        if not gate.is_set():
            await asyncio.sleep(0.4)
            gate.set()
        return await orig(c, ref_id, source, **kw)

    monkeypatch.setattr(claims, "check_claim", slow_first)
    await pipeline.run("text", [])
    # ref 1 grabbed the worker first; afterwards the problem ref 3 jumps ahead of verified ref 2
    assert calls == ["1", "3", "2"]


async def test_verdict_cache_reuses_clean_answers_only(wired, monkeypatch):
    _, calls = wired
    r1 = await pipeline.run("text", [])
    r2 = await pipeline.run("text", [])
    assert len(calls) == 3  # second run served from the disk cache
    assert [c.model_dump(exclude={"note"}) for c in r1.claims] == [
        c.model_dump(exclude={"note"}) for c in r2.claims
    ]
    assert r2.timings.get("n_verdict_cache_hit") == 3

    async def failing(c, ref_id, source, **kw):
        calls.append("x")
        raise RuntimeError("llm down")

    # a different source text => new key; a failed check must not be cached
    async def other_source(audit, client, pdf_to_text):
        rid = audit.reference.id
        return SourceText(ref_id=rid, kind=EvidenceKind.abstract, text=f"u{rid}", origin="openalex")

    monkeypatch.setattr(sources, "fetch_source", other_source)
    monkeypatch.setattr(claims, "check_claim", failing)
    r3 = await pipeline.run("text", [])
    assert all(c.verdict == Verdict.insufficient_evidence for c in r3.claims)
    await pipeline.run("text", [])
    assert calls.count("x") == 6  # retried, not served from cache


async def test_partial_callback_failure_never_breaks_the_audit(wired):
    def boom(_):
        raise RuntimeError("ui bug")

    final = await pipeline.run("text", [], on_partial=boom)
    assert final.complete and len(final.claims) == 3


async def test_retrieval_runs_ahead_of_the_llm_queue(wired, monkeypatch):
    seen = {}

    async def fake_retrieve(sentence, text, embed=None):
        return [f"excerpt for {sentence}"]

    async def check_claim(c, ref_id, source, **kw):
        seen[ref_id] = kw.get("excerpts")
        return ClaimCheck(
            citation_id=c.id,
            ref_id=ref_id,
            sentence=c.sentence,
            verdict=Verdict.supported,
            quote="a b c d e",
            judge=OP,
            skeptic=OP,
        )

    monkeypatch.setattr(claims, "retrieve", fake_retrieve)
    monkeypatch.setattr(claims, "check_claim", check_claim)
    await pipeline.run("text", [])
    assert seen == {r: [f"excerpt for Claim {r} [{r}]."] for r in ("1", "2", "3")}
