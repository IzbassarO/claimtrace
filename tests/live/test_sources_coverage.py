"""Live coverage report for fetch_source over a fixed DOI list (not a pass/fail test).

    RUN_LIVE=1 SOURCES_COVERAGE=1 uv run pytest -s tests/live/test_sources_coverage.py

Metadata (title, authors, year) comes from the free OpenAlex DOI lookup only; pmcid and
oa_pdf_url are left empty, so every full-text source has to find the paper itself.
GROBID is not used: `pdf_to_text` raises (as when GROBID is down), so the chain continues.
A PDF "found" = a candidate passed the size / content-type / %PDF checks; it has NOT been
through the wrong-document guard (that needs the text), so the pdf column is an upper bound.
Classes are exclusive: full_text (JATS, guarded) > pdf > abstract > none.

`test_fast_pdf_report` instead parses every PDF candidate with ingest.pdf_to_text_fast (pdfium,
no GROBID) and reports, per candidate, parse time and whether the wrong-document guard accepts it.
"""

import asyncio
import os
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlsplit

import pytest

import claimtrace.sources as src
from claimtrace.core.http import make_client, request
from claimtrace.core.schemas import Reference, ReferenceAudit, RefStatus, Work
from claimtrace.ingest import pdf_to_text_fast

LIST = Path(__file__).parents[1] / "fixtures/sources/coverage_dois.txt"
DOIS = [ln.strip() for ln in LIST.read_text().splitlines() if ln.strip() and ln[0] != "#"]

pytestmark = pytest.mark.skipif(
    not os.environ.get("SOURCES_COVERAGE"), reason="coverage report: set SOURCES_COVERAGE=1"
)


async def no_grobid(pdf: bytes) -> str:
    raise RuntimeError("GROBID not available")


async def work_for(doi: str, client) -> Work | None:
    r = await request(client, "GET", f"https://api.openalex.org/works/doi:{quote(doi)}")
    if r.status_code != 200:
        return None
    d = r.json()
    authors = [a["author"]["display_name"] for a in d.get("authorships", [])]
    return Work(
        provider="openalex",
        title=d.get("display_name"),
        authors=authors,
        year=d.get("publication_year"),
        doi=doi.lower(),
    )


async def test_coverage_report(monkeypatch):
    pdf_hits: dict[str, list[str]] = {}
    current = {"doi": ""}
    get_pdf = src._get_pdf

    async def spy(client, url):
        got = await get_pdf(client, url)
        if got is not None:
            pdf_hits.setdefault(current["doi"], []).append(url)
        return got

    monkeypatch.setattr(src, "_get_pdf", spy)
    rows = []
    t0 = time.monotonic()
    async with make_client() as c:
        for doi in DOIS:
            current["doi"] = doi
            w = await work_for(doi, c)
            if w is None:
                rows.append((doi, "no_metadata", ""))
                continue
            a = ReferenceAudit(
                reference=Reference(id=doi, raw=doi), status=RefStatus.verified, matched=w
            )
            s = await src.fetch_source(a, c, no_grobid)
            if s and s.kind == "full_text":
                cls, where = "full_text", s.origin
            elif doi in pdf_hits:
                cls, where = "pdf", urlsplit(pdf_hits[doi][0]).hostname
            elif s:
                cls, where = "abstract", s.origin
            else:
                cls, where = "none", ""
            rows.append((doi, cls, where))
            await asyncio.sleep(0.2)
    total = time.monotonic() - t0
    print()
    for row in rows:
        print("  ".join(row))
    counts = Counter(cls for _, cls, _ in rows)
    n = len(rows)
    print(f"\n{n} DOIs in {total:.0f}s")
    for cls in ("full_text", "pdf", "abstract", "none", "no_metadata"):
        print(f"{cls:12} {counts[cls]:3}  {100 * counts[cls] / n:5.1f}%")
    print("pdf hosts:", Counter(w for _, cls, w in rows if cls == "pdf"))


async def test_cache_timing(monkeypatch, tmp_path):
    """fetch_source over the list twice with an empty sources cache: cold vs warm.
    Only the sources cache starts empty (the OpenAlex response cache is the usual one)."""
    s = src.get_settings().model_copy(update={"cache_dir": str(tmp_path)})
    async with make_client() as c:
        works = [await work_for(doi, c) for doi in DOIS]
        monkeypatch.setattr(src, "get_settings", lambda: s)
        audits = [
            ReferenceAudit(reference=Reference(id=str(i), raw=""), status="verified", matched=w)
            for i, w in enumerate(works)
            if w
        ]
        times = []
        for _ in ("cold", "warm"):
            t0 = time.monotonic()
            got = [await src.fetch_source(a, c, no_grobid) for a in audits]
            times.append((time.monotonic() - t0, sum(g is not None for g in got)))
    cached = len(list((tmp_path / "sources").glob("*.json")))
    print(f"\n{len(audits)} DOIs, {cached} cached")
    for label, (t, n) in zip(("cold", "warm"), times, strict=True):
        print(f"{label}: {t:6.1f}s  ({n} with text, {t / len(audits) * 1000:.0f} ms/DOI)")


async def test_fast_pdf_report(monkeypatch, tmp_path):
    s = src.get_settings().model_copy(update={"cache_dir": str(tmp_path)})
    monkeypatch.setattr(src, "get_settings", lambda: s)  # empty sources cache: real fetches
    parses: list[tuple[str, float, str]] = []  # (doi, seconds, outcome)
    current = {"doi": "", "w": None}

    async def timed_fast(pdf: bytes) -> str:
        t0 = time.monotonic()
        try:
            text = await pdf_to_text_fast(pdf)
        except Exception as e:
            parses.append((current["doi"], time.monotonic() - t0, f"error:{type(e).__name__}"))
            raise
        w = current["w"]
        ok = src.looks_like(text, w.title, w.authors)
        parses.append((current["doi"], time.monotonic() - t0, "accepted" if ok else "rejected"))
        return text

    rows, per_doi = [], []
    async with make_client() as c:
        for doi in DOIS:
            w = await work_for(doi, c)
            if w is None:
                rows.append((doi, "no_metadata", ""))
                continue
            current.update(doi=doi, w=w)
            a = ReferenceAudit(
                reference=Reference(id=doi, raw=doi), status=RefStatus.verified, matched=w
            )
            t0 = time.monotonic()
            got = await src.fetch_source(a, c, timed_fast)
            per_doi.append(time.monotonic() - t0)
            if got is None:
                rows.append((doi, "none", ""))
            else:
                where = got.origin + ("-html" if "/html/" in (got.url or "") else "")
                rows.append((doi, got.kind, where))
            await asyncio.sleep(0.2)
    print()
    for doi, cls, where in rows:
        mine = [f"{o} {t * 1000:.0f}ms" for d, t, o in parses if d == doi]
        print(f"{doi:45} {cls:10} {where:18} {'; '.join(mine)}")
    n = len(rows)
    counts = Counter(cls for _, cls, _ in rows)
    print(f"\n{n} DOIs, mean {sum(per_doi) / len(per_doi):.2f} s per source")
    for cls in ("full_text", "abstract", "none", "no_metadata"):
        print(f"{cls:12} {counts[cls]:3}  {100 * counts[cls] / n:5.1f}%")
    print("full_text by origin:", Counter(w for _, cls, w in rows if cls == "full_text"))
    outcomes = Counter(o for _, _, o in parses)
    times = sorted(t for _, t, _ in parses)
    if times:
        print(f"PDF parses: {len(times)}, {dict(outcomes)}")
        print(f"parse ms: mean {1000 * sum(times) / len(times):.0f}, max {1000 * times[-1]:.0f}")
