"""job.error is sent to the browser verbatim: secrets in exception text must be redacted."""

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
