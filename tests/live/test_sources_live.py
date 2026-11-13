"""Live: hits Europe PMC / OpenAlex / Semantic Scholar / Crossref. Runs only with RUN_LIVE=1."""

from claimtrace.core.http import make_client
from claimtrace.core.schemas import Reference, ReferenceAudit, RefStatus, Work
from claimtrace.sources import fetch_source


async def no_grobid(pdf: bytes) -> str:
    raise RuntimeError("GROBID not used in this test")


def audit(**work) -> ReferenceAudit:
    return ReferenceAudit(
        reference=Reference(id="1", raw="x"),
        status=RefStatus.verified,
        matched=Work(provider="crossref", **work),
    )


async def test_europepmc_full_text_by_doi():
    a = audit(title="Array programming with NumPy", doi="10.1038/s41586-020-2649-2")
    async with make_client() as c:
        s = await fetch_source(a, c, no_grobid)
    assert s is not None and s.kind == "full_text" and s.origin == "europepmc"
    assert "NumPy" in s.text[:2000] and len(s.text) > 10000


async def test_abstract_by_doi_when_no_full_text():
    a = audit(
        title="ImageNet classification with deep convolutional neural networks",
        doi="10.1145/3065386",
    )
    async with make_client() as c:
        s = await fetch_source(a, c, no_grobid)
    assert s is not None and s.kind == "abstract" and len(s.text) > 200
