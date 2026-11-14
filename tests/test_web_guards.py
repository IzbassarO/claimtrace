"""Deployment guards: daily cap, queue position, offline country lookup, usage endpoint."""

import asyncio
import gzip
import logging
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claimtrace import pipeline
from claimtrace.core.schemas import Report
from claimtrace.core.settings import get_settings
from claimtrace.web import app as web
from claimtrace.web import guards

GEO = Path(__file__).parent / "fixtures" / "web" / "dbip-country-lite.csv"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOIP_CSV", str(GEO))
    get_settings.cache_clear()
    web.JOBS.clear()

    async def fake_run(text, files=None, on_progress=None, report_id=None, on_partial=None):
        return Report(id=report_id)

    monkeypatch.setattr(pipeline, "run", fake_run)
    with TestClient(web.app) as c:
        yield c
    web.JOBS.clear()
    get_settings.cache_clear()


def post(client, ip="2.16.1.2"):
    return client.post("/api/jobs", data={"text": "x"}, headers={"X-Forwarded-For": ip})


def test_country_lookup(tmp_path, monkeypatch):
    gz = tmp_path / "geo.csv.gz"
    gz.write_bytes(gzip.compress(GEO.read_bytes()))
    shuffled = tmp_path / "shuffled.csv"  # lookups must not rely on the file being sorted
    shuffled.write_text("".join(reversed(GEO.read_text().splitlines(keepends=True))))
    for path in (str(GEO), str(gz), str(shuffled)):
        monkeypatch.setenv("GEOIP_CSV", path)
        assert guards.country("1.0.0.7") == "AU"
        assert guards.country("2.16.255.255") == "KZ"
        assert guards.country("2001:4860:4860::8888") == "US"
        assert guards.country("::ffff:1.0.0.1") == "AU"  # IPv4-mapped
        assert guards.country("2a00:1450::1") == "IE"
        assert guards.country("9.9.9.9") == "??"  # gap between ranges
        assert guards.country("5.5.5.5") == "??"  # ZZ = unknown in db-ip
        assert guards.country("2c0f::1") == "??"  # past the last IPv6 range
    for local in ("10.1.2.3", "192.168.0.1", "127.0.0.1", "::1", "fe80::1", "fd00::1"):
        assert guards.country(local) == "LOCAL"
    assert guards.country("testclient") == "??"
    monkeypatch.setenv("GEOIP_CSV", str(tmp_path / "missing.csv.gz"))
    assert guards.country("1.0.0.7") == "??"


def test_daily_cap_429_and_survives_restart(client, monkeypatch):
    monkeypatch.setenv("MAX_JOBS_PER_DAY", "3")
    get_settings.cache_clear()
    assert all(post(client, f"2.16.0.{i}").status_code == 200 for i in range(3))
    r = post(client, "1.0.0.1")
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail.startswith("Daily limit of 3 audits reached on this server; it resets at 00:00")
    assert " h " in detail and detail.endswith(" min).")
    assert client.get("/api/info").json() == {
        "max_reports": web.MAX_REPORTS,
        "jobs_today": 3,
        "daily_limit": 3,
    }
    # restart: fresh process state, same CACHE_DIR
    web.JOBS.clear()
    with TestClient(web.app) as c2:
        assert post(c2, "1.0.0.1").status_code == 429
        assert c2.get("/api/info").json()["jobs_today"] == 3
    assert guards.load() == {guards.today(): {"KZ": 3}}


def test_rejected_jobs_are_not_counted(client):
    assert client.post("/api/jobs", data={}).status_code == 400
    for i in range(web.MAX_QUEUED):
        web.JOBS[f"q{i}"] = web.Job(id=f"q{i}")
    assert post(client).status_code == 503
    assert guards.jobs_today() == 0


def test_queue_position(client, monkeypatch):
    release = threading.Event()

    async def slow(text, files=None, on_progress=None, report_id=None, on_partial=None):
        await asyncio.to_thread(release.wait, 5)
        return Report(id=report_id)

    monkeypatch.setattr(pipeline, "run", slow)
    ids = [post(client, f"2.16.0.{i}").json()["id"] for i in range(5)]  # 2 run, 3 wait
    time.sleep(0.2)
    jobs = [client.get(f"/api/jobs/{i}").json() for i in ids]
    assert [j["status"] for j in jobs] == ["running"] * 2 + ["queued"] * 3
    assert [j["queue_position"] for j in jobs] == [None, None, 1, 2, 3]
    assert {j["queue_length"] for j in jobs} == {3}
    release.set()
    for i in ids:
        end = time.monotonic() + 5
        while (j := client.get(f"/api/jobs/{i}").json())["status"] != "done":
            assert time.monotonic() < end
            time.sleep(0.01)
        assert j["queue_position"] is None
    # only once every job has finished is the queue guaranteed to be empty
    assert all(client.get(f"/api/jobs/{i}").json()["queue_length"] == 0 for i in ids)


def test_max_running_jobs_from_settings(client, monkeypatch):
    monkeypatch.setenv("MAX_RUNNING_JOBS", "1")
    get_settings.cache_clear()
    with TestClient(web.app):  # lifespan reads the setting
        assert web.app.state.running._value == 1


def test_usage_endpoint(client, monkeypatch):
    bearer = {"Authorization": "Bearer x"}
    assert client.get("/api/usage", headers=bearer).status_code == 404  # disabled by default
    monkeypatch.setenv("USAGE_TOKEN", "s3cret-token")
    get_settings.cache_clear()
    assert client.get("/api/usage").status_code == 403
    assert client.get("/api/usage", headers={"Authorization": "Bearer wrong"}).status_code == 403
    post(client, "2.16.0.1")
    post(client, "2.16.0.2")
    post(client, "2001:4860::1")
    post(client, "10.0.0.1")
    assert client.get("/api/usage?token=s3cret-token").status_code == 403  # never via query
    r = client.get("/api/usage", headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["days"]) == 30 and body["days"][0]["date"] == guards.today()
    assert body["days"][0]["total"] == 4
    assert body["countries"] == {"KZ": 2, "US": 1, "LOCAL": 1}
    assert list(body["countries"])[0] == "KZ" and body["total"] == 4


def test_no_ip_is_stored_or_logged(client, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    ips = ["2.16.7.9", "2001:4860:abcd::42", "10.9.8.7"]
    for ip in ips:
        assert post(client, ip).status_code == 200
    stored = "".join(p.read_text() for p in (tmp_path / "usage").rglob("*") if p.is_file())
    assert stored and guards.load()[guards.today()] == {"KZ": 1, "US": 1, "LOCAL": 1}
    for ip in ips:
        assert ip not in stored and ip not in caplog.text
