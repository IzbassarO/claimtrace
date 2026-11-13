"""Streamed and saved reports in the UI (app.js run in node with a fake DOM, see
test_web_report_view.py): a partial reference list must not read as final, and the saved sample
must not carry another report's link. Skipped without node."""

import asyncio
import json
import shutil
import subprocess

import pytest
from test_web_report_view import HARNESS, JS, render_calls, text

from claimtrace import claims, ingest, pipeline, refaudit, sources
from claimtrace.core.schemas import (
    Citation,
    ClaimCheck,
    EvidenceKind,
    Manuscript,
    Reference,
    ReferenceAudit,
    RefStatus,
    Report,
    SourceText,
    Verdict,
    Work,
)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: job.stage leaves references while refs still audit; UI shows the list as final",
)
async def test_partial_reference_list_is_not_shown_as_final(monkeypatch):
    # Ref 1 is verified fast and cited; refs 2 and 3 are slow and end up not_found. The web job
    # exposes (job.stage, job.partial) to the poller exactly as recorded here.
    refs = [Reference(id=str(i), raw=f"r{i}", title=f"Paper {i}", year=2020) for i in (1, 2, 3)]

    async def parse_inputs(text, files):
        cit = Citation(id="c1", sentence="Claim one [1].", ref_ids=["1"])
        return Manuscript(body=text, references=list(refs), citations=[cit])

    async def audit_references(refs, client, on_progress=None, on_result=None, priority=None):
        out = []
        for i, r in enumerate(refs):
            await asyncio.sleep(0.05 if i else 0)
            a = ReferenceAudit(
                reference=r,
                status=RefStatus.verified if i == 0 else RefStatus.not_found,
                matched=Work(provider="crossref", title=r.title) if i == 0 else None,
            )
            on_result(i, a)
            on_progress(i + 1, len(refs))
            out.append(a)
        return out

    async def fetch_source(audit, client, pdf_to_text):
        rid = audit.reference.id
        return SourceText(ref_id=rid, kind=EvidenceKind.abstract, text="t", origin="openalex")

    async def check_claim(c, ref_id, source, **kw):
        v = Verdict.insufficient_evidence
        return ClaimCheck(citation_id=c.id, ref_id=ref_id, sentence=c.sentence, verdict=v)

    monkeypatch.setattr(ingest, "parse_inputs", parse_inputs)
    monkeypatch.setattr(refaudit, "audit_references", audit_references)
    monkeypatch.setattr(sources, "fetch_source", fetch_source)
    monkeypatch.setattr(claims, "check_claim", check_claim)

    job: dict = {}
    polls: list[tuple[str, Report]] = []  # what a poll between two events would see

    def progress(stage, done, total):
        job["stage"] = stage
        if job.get("partial"):
            polls.append((stage, job["partial"]))

    await pipeline.run("x", [], progress, on_partial=lambda r: job.__setitem__("partial", r))
    early = [(s, p) for s, p in polls if len(p.references) < len(refs)]
    assert early  # the run did stream a partial reference list
    calls = [
        [p.model_dump(mode="json"), {"job": {"status": "running", "stage": s}}] for s, p in early
    ]
    for (stage, _), call in zip(early, calls, strict=True):
        out = render_calls([call])
        refs_text = text(out["refs"])
        # Either the 'Still checking…' marker is shown, or the text says 'so far'; it must
        # never claim 'No references need attention.' while not_found refs are still running.
        assert out["refs-wait"]["hidden"] is False, (stage, refs_text)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: saved sample view keeps the previous job's shareable link / copy-link",
)
def test_saved_sample_does_not_offer_the_previous_reports_link():
    # A user audits their own manuscript (job A), clicks "Audit another manuscript", then
    # "Try the sample". The sample is shown with "Shareable link (anyone with it can view this
    # report)" still pointing at job A: sharing it exposes their manuscript's report.
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    live = Report(
        id="A",
        references=[
            ReferenceAudit(reference=Reference(id="1", raw="r1"), status=RefStatus.verified)
        ],
    )
    sample = Report.model_validate_json((JS.parent / "sample-report.json").read_text())
    harness = HARNESS.replace('"saved", "saved-note", "report"]', '"saved", "permalink", "share"]')
    calls = [
        "jobId = 'AAAAAAAAAAAAAAAAAAAAAA';",
        [live.model_dump(mode="json")],
        "reset();",
        "jobId = null; streamed = false;",  # what trySample() does before rendering
        [sample.model_dump(mode="json"), {"saved": True}],
    ]
    r = subprocess.run(
        [node, "-e", harness, str(JS)],
        input=json.dumps(calls),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["saved"]["hidden"] is False  # the sample view is on screen
    shown_link = text(out["permalink"])
    hidden = out["share"].get("hidden") is True
    assert hidden or "AAAAAAAAAAAAAAAAAAAAAA" not in shown_link, shown_link
