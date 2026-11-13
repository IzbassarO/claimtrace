"""Disk verdict cache (pipeline._cached_verdict): a cache hit must not show what this run would
not have produced. Offline: ingest, refaudit, sources and the LLMs are fakes."""

import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace import claims, ingest, pipeline, refaudit, sources
from claimtrace.core import llm
from claimtrace.core import settings as st
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


def _wire(monkeypatch, cits, texts: dict[str, tuple[str, str]]):
    """texts: ref id -> (source text, source url); every reference is verified and matched."""
    refs = [Reference(id=i, raw=f"r{i}", title=f"Paper {i}", year=2020) for i in texts]

    async def parse_inputs(text, files):
        return Manuscript(body=text, references=list(refs), citations=list(cits))

    async def audit_references(refs, client, on_progress=None, on_result=None, priority=None):
        out = []
        for i, r in enumerate(refs):
            a = ReferenceAudit(
                reference=r,
                status=RefStatus.verified,
                matched=Work(provider="crossref", title=r.title, doi=f"10.1/{r.id}"),
            )
            out.append(a)
            if on_result:
                on_result(i, a)
        return out

    async def fetch_source(audit, client, pdf_to_text):
        text, url = texts[audit.reference.id]
        return SourceText(
            ref_id=audit.reference.id,
            kind=EvidenceKind.abstract,
            text=text,
            origin="crossref",
            url=url,
        )

    monkeypatch.setattr(ingest, "parse_inputs", parse_inputs)
    monkeypatch.setattr(refaudit, "audit_references", audit_references)
    monkeypatch.setattr(sources, "fetch_source", fetch_source)
    monkeypatch.setenv("LLM_CONCURRENCY", "1")
    st.get_settings.cache_clear()


async def test_cache_hit_keeps_the_source_url_of_the_reference_being_checked(monkeypatch):
    # Two distinct references whose abstracts read the same (a preprint and its journal version,
    # two chapters with the publisher's book blurb, a duplicated entry). The claim for [2] must
    # link to [2]'s source, not to [1]'s.
    same = "Treatment X reduced mortality in all treated patients."
    cits = [Citation(id="c1", sentence="X lowers mortality [1, 2].", ref_ids=["1", "2"])]
    _wire(
        monkeypatch,
        cits,
        {"1": (same, "https://arxiv.org/abs/2001.00001"), "2": (same, "https://doi.org/10.1/2")},
    )
    op = ModelOpinion(verdict="supported", quote="reduced mortality in all treated", rationale="r")

    async def check_claim(c, ref_id, source, **kw):
        return ClaimCheck(
            citation_id=c.id,
            ref_id=ref_id,
            sentence=c.sentence,
            verdict=Verdict.supported,
            quote=op.quote,
            evidence_kind=source.kind,
            source_url=source.url,
            judge=op,
            skeptic=op,
        )

    monkeypatch.setattr(claims, "check_claim", check_claim)
    report = await pipeline.run("text", [])
    urls = {c.ref_id: c.source_url for c in report.claims}
    assert urls == {"1": "https://arxiv.org/abs/2001.00001", "2": "https://doi.org/10.1/2"}


async def test_cached_verdict_is_regated_after_a_quote_gate_fix(monkeypatch):
    # The cache stores the final, already-gated verdict. Its key covers the system prompts and
    # models, not the deterministic gate. The quote gate has been fixed many times; every
    # verdict an older gate let through is served unchanged afterwards, on a quote that is not
    # in the source at word boundaries (10 mg vs 110 mg).
    src = "In the trial, the 110 mg dose reduced mortality in all treated patients."
    cits = [Citation(id="c1", sentence="A 10 mg dose reduced mortality [1].", ref_ids=["1"])]
    _wire(monkeypatch, cits, {"1": (src, "https://doi.org/10.1/1")})
    reply = json.dumps(
        {
            "verdict": "supported",
            "quote": "10 mg dose reduced mortality in all treated patients",
            "rationale": "r",
        }
    )
    monkeypatch.setattr(llm, "get_model", lambda role: FakeListChatModel(responses=[reply] * 4))

    real_gate = claims.verify_quote
    # the gate as deployed before a boundary fix: plain substring match
    monkeypatch.setattr(claims, "verify_quote", lambda q, s: q.lower() in s.lower())
    first = await pipeline.run("text", [])
    assert first.claims[0].verdict == Verdict.supported  # what the old gate said

    monkeypatch.setattr(claims, "verify_quote", real_gate)  # the fix is deployed
    assert not real_gate("10 mg dose reduced mortality in all treated patients", src)
    second = await pipeline.run("text", [])
    c = second.claims[0]
    # without the cache this run says insufficient_evidence (no verbatim quote)
    assert c.verdict == Verdict.insufficient_evidence, (c.verdict, c.quote)
