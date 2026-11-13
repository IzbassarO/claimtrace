"""audit_references streaming: on_result per reference as it finishes, priority start order."""

import asyncio

import httpx
import pytest

from claimtrace import refaudit
from claimtrace.core.schemas import Reference, RefStatus
from claimtrace.core.settings import get_settings

TOKENS = [f"zq{c}x" for c in "abcdefgh"]  # one unique word per reference title
REFS = [Reference(id=str(i), raw="", title=f"Study {t} of things") for i, t in enumerate(TOKENS)]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "")
    monkeypatch.setenv("CONTACT_EMAIL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def run(refs, **kw):
    """Slow empty providers; returns (audits, indices in the order Crossref was first asked)."""
    started = []

    async def handler(req: httpx.Request):
        if req.url.host == "api.crossref.org":
            q = req.url.params.get("query.bibliographic", "")
            started.extend(i for i, t in enumerate(TOKENS) if t in q)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"message": {"items": []}})
        return httpx.Response(200, json={"results": [], "meta": {"count": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await refaudit.audit_references(refs, client, **kw)
    return out, started


async def test_on_result_each_index_once_same_objects():
    got = []
    out, _ = await run(REFS, on_result=lambda i, a: got.append((i, a)))
    assert [a.reference.id for a in out] == [r.id for r in REFS]  # input order kept
    assert sorted(i for i, _ in got) == list(range(len(REFS)))
    assert all(out[i] is a for i, a in got)
    assert all(a.status == RefStatus.not_found for a in out)


async def test_priority_starts_first():
    out, started = await run(REFS, priority=[6, 3, 99, 6])  # out-of-range / dupes ignored
    assert started[:2] == [6, 3]
    assert started == [6, 3, 0, 1, 2, 4, 5, 7]
    assert [a.reference.id for a in out] == [r.id for r in REFS]


async def test_default_order_is_input_order():
    _, started = await run(REFS)
    assert started == list(range(len(REFS)))


async def test_raising_callback_does_not_break_audit():
    calls, progress = [], []

    def boom(i, a):
        calls.append(i)
        raise RuntimeError("consumer bug")

    out, _ = await run(REFS, on_result=boom, on_progress=lambda d, t: progress.append(d))
    assert sorted(calls) == list(range(len(REFS)))
    assert progress == list(range(1, len(REFS) + 1))
    assert all(a.status == RefStatus.not_found for a in out)


async def test_arxiv_batch_refs_still_stream():
    """References waiting on the arXiv batch finish later but are still reported once."""
    refs = [
        *REFS[:2],
        Reference(id="ax", raw="", title="Study zqzx of things", arxiv_id="1706.03762"),
    ]
    arxiv_xml = '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    got = []

    async def handler(req: httpx.Request):
        if req.url.host == "export.arxiv.org":
            await asyncio.sleep(0.05)
            return httpx.Response(200, text=arxiv_xml)
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": []}})
        return httpx.Response(200, json={"results": [], "meta": {"count": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await refaudit.audit_references(
            refs, client, on_result=lambda i, a: got.append(i), priority=[2]
        )
    assert got[-1] == 2 and sorted(got) == [0, 1, 2]
    assert any(p.provider == "arxiv" and p.ok for p in out[2].providers)
