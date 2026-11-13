import asyncio
import json
import os
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
            section="3 Results",
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
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    get_settings.cache_clear()
    web.JOBS.clear()
    calls = []

    async def fake_run(text, files=None, on_progress=None, report_id=None, on_partial=None):
        calls.append((text, files))
        on_progress("references", 1, 3)
        return make_report(report_id)

    monkeypatch.setattr(pipeline, "run", fake_run)
    with TestClient(web.app) as c:
        c.calls = calls
        c.reports = tmp_path / "reports"
        yield c
    web.JOBS.clear()
    get_settings.cache_clear()


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

    async def slow(text, files=None, on_progress=None, report_id=None, on_partial=None):
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
    assert "- Section: 3 Results" in md
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


def test_job_ids_are_unguessable_tokens(client):
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    assert web.JOB_ID.fullmatch(job_id)
    wait(client, job_id)


def test_report_survives_restart(client, monkeypatch):
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    wait(client, job_id)
    assert (client.reports / f"{job_id}.json").is_file()
    assert not list(client.reports.glob("*.tmp"))
    web.JOBS.clear()  # simulated restart

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "done" and job["report"]["id"] == job_id
    assert Report.model_validate(job["report"]).claims[0].quote == QUOTE
    assert Report.model_validate(client.get(f"/api/jobs/{job_id}/export.json").json()).id == job_id
    assert QUOTE in client.get(f"/api/jobs/{job_id}/export.md").text

    async def fake_ask_text(role, system, user):
        return "ok"

    monkeypatch.setattr(chat, "ask_text", fake_ask_text)
    assert client.post(f"/api/jobs/{job_id}/chat", json={"question": "q"}).json() == {
        "answer": "ok"
    }


def test_bad_ids_404_without_touching_disk(client, monkeypatch):
    def no_disk(*a, **k):
        raise AssertionError("filesystem touched")

    monkeypatch.setattr(web, "_load", no_disk)
    for bad in ("nope", "....", "a" * 21, "a" * 23, "%2e%2e%2fetc%2fpasswd_aaaa", "a.b" * 8):
        assert client.get(f"/api/jobs/{bad}").status_code == 404, bad
        assert client.get(f"/api/jobs/{bad}/export.json").status_code == 404, bad
        assert client.get(f"/r/{bad}").status_code == 404, bad


def test_unknown_valid_id_404(client):
    assert client.get(f"/api/jobs/{'a' * 22}").status_code == 404


def test_permalink_serves_page(client):
    page = client.get(f"/r/{'A_-' * 7}x")
    assert page.status_code == 200 and 'id="permalink"' in page.text


def test_retention_keeps_newest(client, monkeypatch):
    monkeypatch.setattr(web, "MAX_REPORTS", 3)
    d = client.reports
    d.mkdir()
    for i in range(3):
        (d / f"old{i}.json").write_text("{}")
        os.utime(d / f"old{i}.json", (1000 + i, 1000 + i))
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    wait(client, job_id)
    assert sorted(f.name for f in d.glob("*.json")) == sorted(
        [f"{job_id}.json", "old1.json", "old2.json"]
    )


def test_disk_write_failure_keeps_job_done(client):
    client.reports.write_text("not a directory")  # mkdir fails
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    job = wait(client, job_id)
    assert job["status"] == "done" and job["report"]["id"] == job_id
    assert client.get(f"/api/jobs/{job_id}/export.md").status_code == 200


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


def test_upload_limit_rejects_before_parsing(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    big = b"a" * (2 * 1024 * 1024 + 10)  # over max + 1 MB slack
    # Content-Length path
    r = client.post("/api/jobs", files=[("files", ("a.txt", big, "text/plain"))])
    assert r.status_code == 413 and "max 1 MB" in r.json()["detail"]
    # chunked path (no Content-Length): counted while streaming
    body = (
        b'--B\r\nContent-Disposition: form-data; name="files"; filename="a.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\n" + big + b"\r\n--B--\r\n"
    )
    chunks = (body[i : i + 65536] for i in range(0, len(body), 65536))
    ctype = {"content-type": "multipart/form-data; boundary=B"}
    r = client.post("/api/jobs", content=chunks, headers=ctype)
    assert r.status_code == 413 and "content-length" not in r.request.headers
    assert not web.JOBS and not client.calls


def test_queue_refuses_beyond_four_waiting(client):
    for i in range(web.MAX_QUEUED):
        web.JOBS[f"q{i}"] = web.Job(id=f"q{i}")  # queued
    r = client.post("/api/jobs", data={"text": "x"})
    assert r.status_code == 503 and "waiting" in r.json()["detail"]
    del web.JOBS["q0"]
    assert client.post("/api/jobs", data={"text": "x"}).status_code == 200


def test_job_timeout_is_error_without_report(client, monkeypatch):
    async def hang(text, files=None, on_progress=None, report_id=None, on_partial=None):
        await asyncio.sleep(10)

    monkeypatch.setattr(pipeline, "run", hang)
    monkeypatch.setattr(web, "JOB_TIMEOUT_S", 0.05)
    job = wait(client, client.post("/api/jobs", data={"text": "x"}).json()["id"])
    assert job["status"] == "error" and "timed out" in job["error"] and job["report"] is None


def test_job_rate_limit_per_client_ip(client):
    def post(ip):
        return client.post("/api/jobs", data={"text": "x"}, headers={"X-Forwarded-For": ip})

    assert all(post("1.2.3.4, 10.0.0.1").status_code == 200 for _ in range(web.RATE_JOBS))
    r = post("1.2.3.4")
    assert r.status_code == 429
    assert post("5.6.7.8").status_code == 200  # other clients are not affected


def test_chat_busy_and_per_report_limit(client, monkeypatch):
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    wait(client, job_id)

    async def fake_answer(report, question, history):
        return "ok"

    monkeypatch.setattr(chat, "answer", fake_answer)
    ask = lambda: client.post(f"/api/jobs/{job_id}/chat", json={"question": "q"})  # noqa: E731
    sem = web.app.state.chat_running
    for _ in range(web.MAX_CHAT_RUNNING):
        sem._value -= 1  # occupy every slot, as two running chats would
    r = ask()
    assert r.status_code == 429 and "busy" in r.json()["detail"]
    for _ in range(web.MAX_CHAT_RUNNING):
        sem._value += 1
    assert all(ask().status_code == 200 for _ in range(web.MAX_CHAT_PER_REPORT))
    r = ask()
    assert r.status_code == 429 and "limit" in r.json()["detail"]


def test_ui_shareable_link_wording():
    page = (Path(web.STATIC) / "index.html").read_text()
    assert "Permanent" not in page and "Shareable link" in page and "newest 1000" in page


def test_partial_report_is_served_while_running_and_not_saved(client, monkeypatch):
    gate = threading.Event()

    async def streaming(text, files=None, on_progress=None, report_id=None, on_partial=None):
        on_progress("references", 1, 3)
        full = make_report(report_id)
        for n in (1, 2):
            on_partial(
                full.model_copy(
                    update={"references": full.references[:n], "claims": [], "complete": False}
                )
            )
        await asyncio.to_thread(gate.wait, 5)
        return full

    monkeypatch.setattr(pipeline, "run", streaming)
    job_id = client.post("/api/jobs", data={"text": "x"}).json()["id"]
    end = time.monotonic() + 5
    while (job := client.get(f"/api/jobs/{job_id}").json())["report"] is None:
        assert time.monotonic() < end
        time.sleep(0.01)
    assert job["status"] == "running" and job["report"]["complete"] is False
    assert [a["reference"]["id"] for a in job["report"]["references"]] == ["1", "2"]
    assert not (client.reports / f"{job_id}.json").exists()  # partial snapshots stay in memory
    assert client.get(f"/api/jobs/{job_id}/export.json").status_code == 409
    gate.set()
    job = wait(client, job_id)
    assert job["report"]["complete"] is True and len(job["report"]["references"]) == 3
    saved = Report.model_validate_json((client.reports / f"{job_id}.json").read_text())
    assert saved.complete and len(saved.claims) == 2


def test_saved_sample_report_matches_sample_manuscript():
    static = Path(web.__file__).parent / "static"
    report = Report.model_validate_json((static / "sample-report.json").read_text())
    assert report.complete
    body, section, _ = split_manuscript((static / "sample.txt").read_text())
    refs = [Reference(id=n, raw=raw) for n, raw in split_entries(section)]
    cites, _ = find_citations(body, refs)
    assert [a.reference.id for a in report.references] == [r.id for r in refs]
    assert [a.reference.raw for a in report.references] == [r.raw for r in refs]
    assert [(c.ref_id, c.sentence) for c in report.claims] == [
        (rid, c.sentence) for c in cites for rid in c.ref_ids
    ]
    # served next to the page, and the UI labels it as a saved example
    assert TestClient(web.app).get("/static/sample-report.json").status_code == 200
    js = (static / "app.js").read_text()
    assert "Saved example — computed on" in js and "results may differ in a live run" in js
