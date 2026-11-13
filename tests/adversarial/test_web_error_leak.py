"""job.error is sent to the browser verbatim: secrets in exception text must be redacted."""

import logging
import time

import httpx
from fastapi.testclient import TestClient

from claimtrace import pipeline
from claimtrace.web import app as web

SECRET = "sk-openalex-SECRET123"


def test_job_error_redacts_api_key(monkeypatch):
    url = f"https://api.openalex.org/works?filter=doi:10.1/x&api_key={SECRET}"

    async def boom(text, files=None, on_progress=None, report_id=None):
        req = httpx.Request("GET", url)
        resp = httpx.Response(429, request=req)
        resp.raise_for_status()  # HTTPStatusError: "... for url '<url with api_key>'"

    monkeypatch.setattr(pipeline, "run", boom)
    web.JOBS.clear()
    with TestClient(web.app) as client:
        job_id = client.post("/api/jobs", data={"text": "Body [1]."}).json()["id"]
        end = time.monotonic() + 5
        while (job := client.get(f"/api/jobs/{job_id}").json())["status"] not in ("done", "error"):
            assert time.monotonic() < end
            time.sleep(0.01)
    web.JOBS.clear()
    assert job["status"] == "error"
    assert SECRET not in (job["error"] or "")


def test_job_failure_log_redacts_api_key(monkeypatch, caplog):
    url = f"https://api.openalex.org/works?filter=doi:10.1/x&api_key={SECRET}"

    async def boom(text, files=None, on_progress=None, report_id=None):
        req = httpx.Request("GET", url)
        httpx.Response(429, request=req).raise_for_status()

    monkeypatch.setattr(pipeline, "run", boom)
    web.JOBS.clear()
    with caplog.at_level(logging.DEBUG, logger="claimtrace.web"), TestClient(web.app) as client:
        job_id = client.post("/api/jobs", data={"text": "Body [1]."}).json()["id"]
        end = time.monotonic() + 5
        while client.get(f"/api/jobs/{job_id}").json()["status"] not in ("done", "error"):
            assert time.monotonic() < end
            time.sleep(0.01)
    web.JOBS.clear()
    failed = [r for r in caplog.records if "failed" in r.getMessage()]
    assert failed and "HTTPStatusError" in failed[0].getMessage()
    for r in caplog.records:
        assert SECRET not in r.getMessage() and r.exc_info is None
        assert SECRET not in (r.exc_text or "")
