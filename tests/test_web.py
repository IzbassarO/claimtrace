import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claimtrace import pipeline
from claimtrace.core.schemas import (
    ClaimCheck,
    EvidenceKind,
    Mismatch,
    ModelOpinion,
    ProviderOutcome,
    Reference,
    ReferenceAudit,
    RefStatus,
    Report,
    Verdict,
    Work,
)
from claimtrace.core.settings import get_settings
from claimtrace.ingest.text import find_citations, split_entries, split_manuscript
from claimtrace.web import app as web
from claimtrace.web import chat, export

QUOTE = "mean blood pressure fell by 10 mmHg in the treated group"


def make_report(report_id="r1") -> Report:
    refs = [
        ReferenceAudit(
            reference=Reference(id="1", raw="Doe J. Salt and blood pressure. 2020.", year=2020),
            status=RefStatus.verified,
            matched=Work(provider="crossref", title="Salt and blood pressure", doi="10.1/x"),
        ),
        ReferenceAudit(
            reference=Reference(id="2", raw="Roe K. Coffee is not harmful. 2019."),
            status=RefStatus.metadata_mismatch,
            matched=Work(provider="openalex", title="Coffee is harmful", year=2019),
            mismatches=[
                Mismatch(field="title", cited="Coffee is not harmful", found="Coffee is harmful")
            ],
        ),
        ReferenceAudit(
            reference=Reference(id="3", raw="<script>alert(1)</script> Ghost 2021."),
            status=RefStatus.unchecked,
            providers=[ProviderOutcome(provider="crossref", ok=False, error="HTTP 503")],
        ),
    ]
    claims = [
        ClaimCheck(
            citation_id="c1",
            ref_id="1",
            sentence="Salt reduction lowers blood pressure [1].",
            verdict=Verdict.supported,
            quote=QUOTE,
            evidence_kind=EvidenceKind.abstract,
            source_url="https://example.org/a",
            judge=ModelOpinion(verdict="supported", quote=QUOTE, rationale="Direct match."),
            skeptic=ModelOpinion(verdict="supported", quote=QUOTE, rationale="Agree."),
        ),
        ClaimCheck(
            citation_id="c2",
            ref_id="3",
            sentence="Ghosts exist [3].",
            verdict=Verdict.source_not_found,
        ),
    ]
    return Report(
        id=report_id,
        title="A test manuscript",
        references=refs,
        claims=claims,
        warnings=["Only the first 200 of 250 references audited."],
        summary={
            "references": {"verified": 1, "metadata_mismatch": 1, "unchecked": 1},
            "claims": {"supported": 1, "source_not_found": 1},
        },
    )


@pytest.fixture
def client(monkeypatch):
    web.JOBS.clear()
    calls = []

    async def fake_run(text, files=None, on_progress=None, report_id=None):
        calls.append((text, files))
        on_progress("references", 1, 3)
        return make_report(report_id)

    monkeypatch.setattr(pipeline, "run", fake_run)
    with TestClient(web.app) as c:
        c.calls = calls
        yield c
    web.JOBS.clear()


def wait(client, job_id, until=("done", "error"), timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} stuck in {job['status']}")


def test_healthz_and_index(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and "ClaimTrace" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_ui_never_uses_innerhtml():
    js = (Path(web.__file__).parent / "static" / "app.js").read_text()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in js


def test_job_lifecycle_text(client):
    r = client.post("/api/jobs", data={"text": "Body [1].\n\nReferences\n[1] Doe 2020."})
    assert r.status_code == 200
    job = wait(client, r.json()["id"])
    assert job["status"] == "done" and job["error"] is None
    assert job["report"]["id"] == r.json()["id"]
    assert job["report"]["summary"]["claims"] == {"supported": 1, "source_not_found": 1}
    assert client.calls[0] == ("Body [1].\n\nReferences\n[1] Doe 2020.", [])


def test_job_with_files(client):
    files = [
        ("files", ("../../etc/paper.TXT", b"text body", "text/plain")),
        ("files", ("refs.bib", b"@article{a,title={T}}", "application/x-bibtex")),
    ]
    r = client.post("/api/jobs", files=files)
    assert r.status_code == 200
    wait(client, r.json()["id"])
    text, inputs = client.calls[0]
    assert text is None
    assert [(f.name, f.content) for f in inputs] == [
        ("paper.TXT", b"text body"),
        ("refs.bib", b"@article{a,title={T}}"),
    ]


def test_requires_text_or_file(client):
    assert client.post("/api/jobs", data={"text": "   "}).status_code == 400
    assert client.post("/api/jobs").status_code == 400
    assert not web.JOBS


def test_rejects_unknown_extension(client):
    r = client.post("/api/jobs", files=[("files", ("x.docx", b"zz", "application/octet-stream"))])
    assert r.status_code == 415
    assert "x.docx" in r.json()["detail"]
    assert not web.JOBS


def test_rejects_oversized_upload(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    half = b"a" * (600 * 1024)
    files = [("files", ("a.txt", half, "text/plain")), ("files", ("b.txt", half, "text/plain"))]
    assert client.post("/api/jobs", files=files).status_code == 413
    assert client.post("/api/jobs", files=files[:1]).status_code == 200


def test_pipeline_failure_is_job_error(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("GROBID unavailable")

    monkeypatch.setattr(pipeline, "run", boom)
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    job = wait(client, job_id)
    assert job["status"] == "error" and "GROBID unavailable" in job["error"]
    assert job["report"] is None
    assert client.get(f"/api/jobs/{job_id}/export.json").status_code == 409


def test_unknown_job_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/export.md").status_code == 404


def test_at_most_two_running(client, monkeypatch):
    release = threading.Event()

    async def slow(text, files=None, on_progress=None, report_id=None):
        await asyncio.to_thread(release.wait, 5)
        return make_report(report_id)

    monkeypatch.setattr(pipeline, "run", slow)
    ids = [client.post("/api/jobs", data={"text": f"t{i}"}).json()["id"] for i in range(3)]
    time.sleep(0.2)
    statuses = sorted(client.get(f"/api/jobs/{i}").json()["status"] for i in ids)
    assert statuses == ["queued", "running", "running"]
    release.set()
    assert all(wait(client, i)["status"] == "done" for i in ids)


def test_eviction_keeps_50_and_refuses_when_all_busy(client):
    for i in range(web.MAX_JOBS):
        web.JOBS[f"old{i}"] = web.Job(id=f"old{i}", status="done")
    new = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    assert len(web.JOBS) == web.MAX_JOBS and "old0" not in web.JOBS and new in web.JOBS
    wait(client, new)

    web.JOBS.clear()
    for i in range(web.MAX_JOBS):
        web.JOBS[f"busy{i}"] = web.Job(id=f"busy{i}", status="running")
    assert client.post("/api/jobs", data={"text": "x"}).status_code == 503


def test_exports(client):
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    wait(client, job_id)
    j = client.get(f"/api/jobs/{job_id}/export.json")
    assert j.status_code == 200 and "attachment" in j.headers["content-disposition"]
    assert Report.model_validate(j.json()).claims[0].quote == QUOTE
    md = client.get(f"/api/jobs/{job_id}/export.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert QUOTE in md.text


def test_markdown_content():
    md = export.to_markdown(make_report())
    assert md.startswith("# ClaimTrace report: A test manuscript")
    assert "- References: metadata_mismatch: 1, unchecked: 1, verified: 1" in md
    assert "### [2] metadata_mismatch" in md
    assert "- Mismatch title: Coffee is not harmful -> Coffee is harmful" in md
    assert "- Provider failures: crossref (HTTP 503)" in md
    assert "### c1 -> [1] supported" in md
    assert f'- Verbatim quote (abstract only): "{QUOTE}"' in md
    assert "### c2 -> [3] source_not_found" in md and "(needs review)" not in md
    assert "Only the first 200 of 250" in md


def test_chat_answers_from_wrapped_report(client, monkeypatch):
    seen = {}

    async def fake_ask_text(role, system, user):
        seen.update(role=role, system=system, user=user)
        return " Claim [c1] is supported. "

    monkeypatch.setattr(chat, "ask_text", fake_ask_text)
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    wait(client, job_id)
    r = client.post(
        f"/api/jobs/{job_id}/chat",
        json={
            "question": "Which claims are supported?",
            "history": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        },
    )
    assert r.status_code == 200 and r.json() == {"answer": "Claim [c1] is supported."}
    assert seen["role"] == "judge"
    # report and history go in as untrusted data, the question last
    assert "<report_json_" in seen["user"] and "<conversation_" in seen["user"]
    assert "<script>alert(1)</script>" in seen["user"]
    assert seen["user"].rstrip().endswith("Question: Which claims are supported?")


def test_chat_failure_and_not_ready(client, monkeypatch):
    async def down(*a):
        raise RuntimeError("provider down")

    monkeypatch.setattr(chat, "ask_text", down)
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    wait(client, job_id)
    assert client.post(f"/api/jobs/{job_id}/chat", json={"question": "q"}).status_code == 502
    assert client.post(f"/api/jobs/{job_id}/chat", json={"question": ""}).status_code == 422
    web.JOBS[job_id].report, web.JOBS[job_id].status = None, "running"
    assert client.post(f"/api/jobs/{job_id}/chat", json={"question": "q"}).status_code == 409


def test_chat_report_drops_abstracts():
    rep = make_report()
    rep.references[0].matched.abstract = "LONG ABSTRACT TEXT"
    assert "LONG ABSTRACT TEXT" not in chat.report_json(rep)


def test_chat_report_trims_references_before_claims(monkeypatch):
    rep = make_report()
    full = chat.report_json(rep)
    assert "HTTP 503" in full and "Doe J." in full and "[references truncated]" not in full
    head = len(json.dumps(rep.model_dump(mode="json", exclude={"references"})))
    # room for slim references only: provider outcomes and raw strings go first
    monkeypatch.setattr(chat, "MAX_REPORT_CHARS", len(full) - 50)
    slim = chat.report_json(rep)
    assert "HTTP 503" not in slim and "Doe J." not in slim and "Coffee is harmful" in slim
    assert "[references truncated]" not in slim
    # no room for references at all: claims, summary and warnings survive whole
    monkeypatch.setattr(chat, "MAX_REPORT_CHARS", head + 20)
    cut = chat.report_json(rep)
    assert "[references truncated]" in cut and '"citation_id": "c1"' in cut
    assert cut.startswith(full[: head - 1])


def test_sample_manuscript_is_served_and_parses():
    resp = TestClient(web.app).get("/static/sample.txt")
    assert resp.status_code == 200
    body, section, warning = split_manuscript(resp.text)
    assert section and warning is None
    entries = split_entries(section)
    assert [n for n, _ in entries] == ["1", "2", "3"]
    assert "10.1109/CVPR.2016.90" in entries[0][1]
    assert "10.1371/journal.pmed.0020124" in entries[1][1]
    assert "doi" not in entries[2][1].lower()
    refs = [Reference(id=n, raw=raw) for n, raw in entries]
    cites, warns = find_citations(body, refs)
    assert [c.ref_ids for c in cites] == [["1"], ["2"], ["3"]] and warns == []
