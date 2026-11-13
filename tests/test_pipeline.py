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
