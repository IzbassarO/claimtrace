"""Live coverage report for fetch_source over a fixed DOI list (not a pass/fail test).

    RUN_LIVE=1 SOURCES_COVERAGE=1 uv run pytest -s tests/live/test_sources_coverage.py

Metadata (title, authors, year) comes from the free OpenAlex DOI lookup only; pmcid and
oa_pdf_url are left empty, so every full-text source has to find the paper itself.
GROBID is not used: `pdf_to_text` raises (as when GROBID is down), so the chain continues.
A PDF "found" = a candidate passed the size / content-type / %PDF checks; it has NOT been
through the wrong-document guard (that needs the text), so the pdf column is an upper bound.
Classes are exclusive: full_text (JATS, guarded) > pdf > abstract > none.
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
