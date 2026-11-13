"""Post-review sweep: the GROBID allow-list in the SSRF guard applies to redirect hops too."""

import httpx
import pytest

from claimtrace.core import http
from claimtrace.core import settings as st


@pytest.mark.xfail(strict=True, reason="BUG: public URL can redirect into the internal GROBID")
async def test_redirect_from_public_host_to_grobid_is_blocked(monkeypatch):
    s = st.Settings(_env_file=None, grobid_url="http://grobid:8070")
    monkeypatch.setattr(http, "get_settings", lambda: s)

    async def dns(host, port):
        return {"evil.example": {"93.184.216.34"}}.get(host, {"172.18.0.3"})

    monkeypatch.setattr(http, "_resolve", dns)
    hits = []

    def handler(r):
        hits.append(r.url.host)
        if r.url.host == "evil.example":  # e.g. an oa_pdf_url from a provider record
            return httpx.Response(302, headers={"location": "http://grobid:8070/api/version"})
        return httpx.Response(200, content=b"internal")

    t = http.PublicOnlyTransport(httpx.MockTransport(handler))
    async with httpx.AsyncClient(transport=t, follow_redirects=True) as c:
        with pytest.raises(http.BlockedURL):
            await c.get("http://evil.example/paper.pdf")
    assert "grobid" not in hits
