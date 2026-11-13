"""Live: real extract LLM and a real GROBID (RUN_LIVE=1; GROBID at GROBID_URL)."""

from pathlib import Path

import httpx

from claimtrace.ingest import parse_inputs, pdf_to_text

FX = Path(__file__).parent.parent / "fixtures" / "ingest"


async def test_live_llm_extraction_is_verbatim():
    ms = await parse_inputs((FX / "numeric.md").read_text(), [])
    assert [r.id for r in ms.references] == ["1", "2", "3"]
    assert not any("extraction failed" in w for w in ms.warnings), ms.warnings
    titled = [r for r in ms.references if r.title]
    assert len(titled) >= 2
    for r in titled:
        assert " ".join(r.title.split()).lower() in " ".join(r.raw.split()).lower()


async def test_live_grobid_pdf_to_text():
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as c:
        pdf = (await c.get("https://arxiv.org/pdf/1706.03762")).content
    text = await pdf_to_text(pdf)
    assert text.lower().startswith("attention is all you need")
    assert len(text) > 5000
