"""Adversarial (#112): the daily cap counts exactly the accepted jobs, rolls over at the UTC
day, survives a corrupt store; queue positions after a failed job; /api/usage token only via
the Authorization header. Offline: TestClient + a fake pipeline."""

import asyncio
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

GEO = Path(__file__).parents[1] / "fixtures" / "web" / "dbip-country-lite.csv"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("GEOIP_CSV", str(GEO))
    monkeypatch.setenv("MAX_JOBS_PER_DAY", "3")
    get_settings.cache_clear()
    web.JOBS.clear()

    async def fake_run(text, files=None, on_progress=None, report_id=None, on_partial=None):
        return Report(id=report_id)

    monkeypatch.setattr(pipeline, "run", fake_run)
    with TestClient(web.app) as c:
        yield c
    web.JOBS.clear()
    get_settings.cache_clear()


def post(client, ip="2.16.1.2", **kw):
    kw.setdefault("data", {"text": "x"})
    return client.post("/api/jobs", headers={"X-Forwarded-For": ip}, **kw)


def test_cap_is_exact_and_rejections_are_not_counted(client):
    assert post(client, files={"files": ("a.exe", b"x")}).status_code == 415
    assert post(client, data={"text": "  "}).status_code == 400
    assert [post(client, f"2.16.0.{i}").status_code for i in range(5)] == [200] * 3 + [429] * 2
    assert post(client, "1.0.0.1").status_code == 429
    assert guards.jobs_today() == 3 and guards.load()[guards.today()] == {"KZ": 3}


def test_per_ip_rate_limit_rejections_are_not_counted(client, monkeypatch):
    monkeypatch.setenv("MAX_JOBS_PER_DAY", "100")
    get_settings.cache_clear()
    codes = [post(client, "2.16.4.4").status_code for _ in range(web.RATE_JOBS + 3)]
    assert codes == [200] * web.RATE_JOBS + [429] * 3
    assert guards.jobs_today() == web.RATE_JOBS


def test_cap_rolls_over_at_utc_midnight(client, monkeypatch):
    day = ["2026-11-13"]
    monkeypatch.setattr(guards, "today", lambda: day[0])
    assert all(post(client, f"2.16.0.{i}").status_code == 200 for i in range(3))
    assert post(client, "2.16.0.9").status_code == 429
    day[0] = "2026-11-14"
    assert post(client, "2.16.0.9").status_code == 200
    assert guards.load() == {"2026-11-13": {"KZ": 3}, "2026-11-14": {"KZ": 1}}


def test_corrupt_usage_store_does_not_block_jobs(client, tmp_path):
    store = tmp_path / "usage" / "usage.json"
    store.parent.mkdir(parents=True)
    store.write_text('{"2026-11-1', encoding="utf-8")  # truncated write
    assert post(client).status_code == 200
    assert client.get("/api/info").status_code == 200


def test_queue_position_after_a_failed_job(client, monkeypatch):
    monkeypatch.setenv("MAX_RUNNING_JOBS", "1")
    get_settings.cache_clear()
    gates = [threading.Event() for _ in range(3)]
    calls = iter(range(3))

    async def run(text, files=None, on_progress=None, report_id=None, on_partial=None):
        i = next(calls)
        await asyncio.to_thread(gates[i].wait, 5)
        if i == 0:
            raise RuntimeError("provider exploded")
        return Report(id=report_id)

    monkeypatch.setattr(pipeline, "run", run)
    with TestClient(web.app) as c:
        a, b, d = (post(c, f"2.16.0.{i}").json()["id"] for i in range(3))
        time.sleep(0.2)
        assert [c.get(f"/api/jobs/{i}").json()["queue_position"] for i in (a, b, d)] == [
            None,
            1,
            2,
        ]
        gates[0].set()
        end = time.monotonic() + 5
        while c.get(f"/api/jobs/{b}").json()["status"] != "running":
            assert time.monotonic() < end
            time.sleep(0.01)
        ja, jb, jd = (c.get(f"/api/jobs/{i}").json() for i in (a, b, d))
        assert ja["status"] == "error" and ja["queue_position"] is None
        assert jb["queue_position"] is None and jd["queue_position"] == 1
        assert jd["queue_length"] == 1
        gates[1].set()
        gates[2].set()


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer"},
        {"Authorization": "Basic czNjcmV0LXRva2Vu"},
        {"Authorization": "s3cret-token"},
        {"Authorization": "Bearer s3cret-token-and-more"},
        {"Authorization": "Bearer s3cret-toke"},
        {"X-Usage-Token": "s3cret-token"},
        {"Cookie": "token=s3cret-token"},
    ],
)
def test_usage_token_only_as_bearer(client, monkeypatch, headers):
    monkeypatch.setenv("USAGE_TOKEN", "s3cret-token")
    get_settings.cache_clear()
    assert client.get("/api/usage?token=s3cret-token", headers=headers).status_code == 403
    assert client.get("/api/usage", headers={"Authorization": "bearer s3cret-token"}).is_success


def test_forwarded_for_spoofing_moves_only_the_country(client):
    # behind Caddy the first X-Forwarded-For is the real client; a spoofed list never adds jobs
    assert post(client, "1.0.0.1, 2.16.0.1, 10.0.0.1").status_code == 200
    assert guards.load()[guards.today()] == {"AU": 1}
    assert post(client, "not-an-ip").status_code == 200
    assert guards.jobs_today() == 2 and guards.load()[guards.today()]["??"] == 1
