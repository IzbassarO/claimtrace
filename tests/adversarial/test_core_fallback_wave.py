"""Skeptic fallback (#138): the report must say truthfully which skeptic read each claim.
Offline: no providers, no LLMs; pipeline helpers and eval accounting are called directly."""

import asyncio

import pytest

import eval.run as er
from claimtrace import pipeline
from claimtrace.core import settings as st
from claimtrace.core.schemas import (
    Citation,
    ClaimCheck,
    EvidenceKind,
    Manuscript,
    ModelOpinion,
    Report,
    SourceText,
    Verdict,
)

PRIMARY, FALLBACK = "kimi-k3.1-pro-highspeed", "kimi-k2.7-code-highspeed"
SRC = SourceText(
    ref_id="r1",
    kind=EvidenceKind.abstract,
    text="the drug cut mortality by half",
    origin="crossref",
)
CIT = Citation(id="c1", sentence="The drug cut mortality by half [1].", ref_ids=["r1"])


def _env(monkeypatch, fallback: str, skeptic: str = PRIMARY):
    monkeypatch.setenv("LLM_JUDGE_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_JUDGE_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("LLM_SKEPTIC_PROVIDER", "moonshot")
    monkeypatch.setenv("LLM_SKEPTIC_MODEL", skeptic)
    monkeypatch.setenv("LLM_SKEPTIC_FALLBACK_MODEL", fallback)
    st.get_settings.cache_clear()


def _check(skeptic_model: str) -> ClaimCheck:
    op = ModelOpinion(verdict="unsupported", quote="the drug cut mortality by half", rationale="r")
    return ClaimCheck(
        citation_id="c1",
        ref_id="r1",
        sentence=CIT.sentence,
        verdict=Verdict.unsupported,
        judge=op.model_copy(update={"model": "claude-sonnet-4-5"}),
        skeptic=op.model_copy(update={"model": skeptic_model}),
    )


def _fallback_warned(report: Report) -> bool:
    return any("fallback" in w for w in report.warnings)


def test_cached_fallback_opinion_is_not_passed_off_as_the_primary_skeptic(monkeypatch):
    """Run 1: primary skeptic down, the fallback answered; the opinion is cached under the
    PRIMARY's key. Run 2 (fallback setting removed / changed): cache hit, Details shows the
    fallback model as skeptic, but the "used the fallback skeptic" warning is gone, so the report
    reads as a clean two-reader check by the configured skeptic."""
    _env(monkeypatch, fallback=FALLBACK)
    pipeline._store_verdict(CIT, SRC, _check(FALLBACK))
    _env(monkeypatch, fallback="")  # operator drops / swaps the fallback model
    hit = pipeline._cached_verdict(CIT, "r1", SRC)
    if hit is None:  # not cached under the primary's key: fine, the primary is asked again
        return
    report = pipeline._report("x", Manuscript(body=""), [], [hit], complete=True)
    assert hit.skeptic.model == FALLBACK
    assert _fallback_warned(report), report.warnings


def test_fallback_named_like_the_primary_does_not_flag_every_claim(monkeypatch):
    """LLM_SKEPTIC_FALLBACK_MODEL == LLM_SKEPTIC_MODEL (copy-paste in .env): a healthy run where
    only the primary answered says "N claims used the fallback skeptic" for every claim."""
    _env(monkeypatch, fallback=PRIMARY)
    report = pipeline._report("x", Manuscript(body=""), [], [_check(PRIMARY)] * 3, complete=True)
    assert not _fallback_warned(report), report.warnings


def test_timing_bench_cost_counts_fallback_tokens(monkeypatch):
    """eval timing suite runs pipeline.run (fallback on). Its cost table keeps only
    extract/judge/skeptic tokens, so tokens_*_skeptic_fallback vanish and usd_total under-reports
    while still looking known."""
    _env(monkeypatch, fallback=FALLBACK)
    timings = {
        "n_tokens_in_judge": 1000.0,
        "n_tokens_out_judge": 100.0,
        "n_tokens_in_skeptic_fallback": 5000.0,
        "n_tokens_out_skeptic_fallback": 500.0,
    }

    async def run(text, files, on_partial=None):
        return Report(id="x", timings=timings)

    monkeypatch.setattr(er.pipeline, "run", run)
    row = asyncio.run(er.time_doc("t", []))
    cost = row["cost"]
    tin = sum(v["tokens_in"] for v in cost.values() if isinstance(v, dict))
    assert tin == pytest.approx(6000), cost
