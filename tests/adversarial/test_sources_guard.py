"""Wrong-document guard: another paper's full text must not pass as the cited one."""

import httpx
import pytest

import claimtrace.sources as src
from claimtrace.core.schemas import Reference, ReferenceAudit, RefStatus, Work
from claimtrace.core.settings import Settings

PDF_URL = "https://repo.example.org/paper.pdf"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    settings = Settings(_env_file=None, contact_email="")  # no Unpaywall lookup
    monkeypatch.setattr(src, "get_settings", lambda: settings)


def pdf_client() -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == PDF_URL:
            return httpx.Response(
                200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def fetch(work: Work, pdf_text: str):
    async def pdf_to_text(_: bytes) -> str:
        return pdf_text

    audit = ReferenceAudit(
        reference=Reference(id="1", raw="x"), status=RefStatus.verified, matched=work
    )
    return await src.fetch_source(audit, pdf_client(), pdf_to_text)


async def test_guard_rejects_paper_sharing_only_stopwords_and_generic_words():
    work = Work(
        provider="crossref",
        title="Aspirin for the prevention of colorectal cancer",
        oa_pdf_url=PDF_URL,
    )
    wrong = (
        "Statins for the prevention of cardiovascular disease and cancer\n\n"
        + "Statin therapy lowered LDL cholesterol and major vascular events. " * 40
    )
    got = await fetch(work, wrong)  # never mentions aspirin or colorectal
    assert got is None or got.kind != "full_text"


async def test_guard_rejects_other_paper_for_short_generic_title():
    # LeCun, Bengio, Hinton 2015, doi 10.1038/nature14539 (not in PMC, so PDF route).
    work = Work(
        provider="crossref",
        title="Deep learning",
        authors=["LeCun, Yann", "Bengio, Yoshua", "Hinton, Geoffrey"],
        oa_pdf_url=PDF_URL,
    )
    wrong = (
        "Deep Residual Learning for Image Recognition\nKaiming He Xiangyu Zhang Shaoqing Ren "
        "Jian Sun\n\n" + "Deeper neural networks are more difficult to train. " * 50
    )
    got = await fetch(work, wrong)
    assert got is None or got.kind != "full_text"
