"""Adversarial: GROBID answers 200 with nothing usable; must not look like an empty paper."""

import httpx
import pytest

from claimtrace.core.schemas import InputFile
from claimtrace.ingest import parse_inputs, tei


@pytest.mark.xfail(
    strict=True, reason="BUG: GROBID 200 with non-TEI / empty TEI gives an empty report, no warning"
)
@pytest.mark.parametrize(
    "answer",
    [
        "<html><body>502 Bad Gateway</body></html>",  # proxy error page, well-formed XML
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/><text><body/></text></TEI>',
    ],
)
async def test_unusable_grobid_answer_warns(monkeypatch, answer):
    monkeypatch.setattr(
        tei,
        "make_client",
        lambda timeout=30: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=answer))
        ),
    )
    ms = await parse_inputs(None, [InputFile(name="paper.pdf", content=b"%PDF-1.4")])
    assert ms.body == "" and ms.citations == []
    assert ms.warnings  # user must learn nothing was read, not see "0 citations"
