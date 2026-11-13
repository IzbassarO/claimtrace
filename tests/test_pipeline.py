import asyncio

from claimtrace import claims, ingest, pipeline, refaudit, sources
from claimtrace.core.schemas import (
    Citation,
    ClaimCheck,
    EvidenceKind,
    Manuscript,
    Reference,
    ReferenceAudit,
    RefStatus,
    SourceText,
    Verdict,
    Work,
)


async def test_pipeline_wires_modules(monkeypatch):
    ref = Reference(id="1", raw="Doe J. A paper. 2020.", title="A paper", year=2020)
    cit = Citation(id="c1", sentence="X is true [1].", ref_ids=["1", "9"], marker="[1]")

    async def parse_inputs(text, files):
        return Manuscript(body=text, references=[ref], citations=[cit])

    async def audit_references(refs, client, on_progress=None):
        return [
            ReferenceAudit(
                reference=refs[0],
                status=RefStatus.verified,
                matched=Work(provider="crossref", title="A paper"),
            )
        ]

    async def fetch_source(audit, client, pdf_to_text):
        return SourceText(ref_id="1", kind=EvidenceKind.abstract, text="t", origin="openalex")

    async def check_claim(c, ref_id, source):
        assert source is not None
        raise RuntimeError("llm down")

    monkeypatch.setattr(ingest, "parse_inputs", parse_inputs)
    monkeypatch.setattr(refaudit, "audit_references", audit_references)
    monkeypatch.setattr(sources, "fetch_source", fetch_source)
    monkeypatch.setattr(claims, "check_claim", check_claim)

    stages = []
    rep = await pipeline.run("body", on_progress=lambda s, d, t: stages.append(s))
    assert rep.summary["references"] == {"verified": 1}
    # unknown ref "9" is skipped; LLM failure degrades to insufficient_evidence + review
    assert [c.verdict for c in rep.claims] == [Verdict.insufficient_evidence]
    assert rep.claims[0].needs_review
    assert stages[0] == "ingest" and stages[-1] == "done"
    assert isinstance(rep.claims[0], ClaimCheck)


async def test_overlong_sentence_and_slow_source_are_bounded(monkeypatch):
    refs = [Reference(id=str(i), raw=f"r{i}", title=f"Paper {i}", year=2020) for i in (1, 2)]
    huge = Citation(id="c1", sentence="x " * 5000 + "[1]", ref_ids=["1"], marker="[1]")
    ok = Citation(id="c2", sentence="Y holds [2].", ref_ids=["2"], marker="[2]")

    async def parse_inputs(text, files):
        return Manuscript(body=text, references=refs, citations=[huge, ok])

    async def audit_references(rs, client, on_progress=None):
        return [
            ReferenceAudit(
                reference=r, status=RefStatus.verified, matched=Work(provider="crossref", title="t")
            )
            for r in rs
        ]

    async def fetch_source(audit, client, pdf_to_text):
        await asyncio.sleep(3600)  # slow-drip server: must hit the per-source deadline

    async def check_claim(c, ref_id, source):
        return ClaimCheck(
            citation_id=c.id, ref_id=ref_id, sentence=c.sentence, verdict=Verdict.source_not_found
        )

    monkeypatch.setattr(ingest, "parse_inputs", parse_inputs)
    monkeypatch.setattr(refaudit, "audit_references", audit_references)
    monkeypatch.setattr(sources, "fetch_source", fetch_source)
    monkeypatch.setattr(claims, "check_claim", check_claim)
    monkeypatch.setattr(pipeline, "SOURCE_TIMEOUT_S", 0.05)
    report = await pipeline.run("text", [])
    assert [c.citation_id for c in report.claims] == ["c2"]
    assert any("longer than" in w for w in report.warnings)


async def test_report_carries_stage_timings(monkeypatch):
    ref = Reference(id="1", raw="r", title="A paper", year=2020)
    cit = Citation(id="c1", sentence="X holds [1].", ref_ids=["1"], marker="[1]")

    async def parse_inputs(text, files):
        return Manuscript(body=text, references=[ref], citations=[cit])

    async def audit_references(refs, client, on_progress=None):
        if on_progress:
            on_progress(1, 1)
        return [
            ReferenceAudit(
                reference=refs[0], status=RefStatus.verified, matched=Work(provider="crossref")
            )
        ]

    async def fetch_source(audit, client, pdf_to_text):
        return SourceText(ref_id="1", kind=EvidenceKind.abstract, text="t", origin="openalex")

    async def check_claim(c, ref_id, source):
        return ClaimCheck(
            citation_id=c.id, ref_id=ref_id, sentence=c.sentence, verdict=Verdict.source_not_found
        )

    monkeypatch.setattr(ingest, "parse_inputs", parse_inputs)
    monkeypatch.setattr(refaudit, "audit_references", audit_references)
    monkeypatch.setattr(sources, "fetch_source", fetch_source)
    monkeypatch.setattr(claims, "check_claim", check_claim)
    t = (await pipeline.run("text", [])).timings
    for k in ("t_ingest_end", "t_references_first", "t_claims_end", "t_total"):
        assert k in t, k
    assert t["n_claim_check"] == 1 and t["n_source_fetch"] == 1 and t["busy_ingest"] >= 0
