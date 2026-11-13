"""GROBID: PDF -> TEI XML -> title, body, references, citations (from <ref type="bibr">)."""

import re
import xml.etree.ElementTree as ET

import httpx

from claimtrace.core.http import make_client, request
from claimtrace.core.schemas import Citation, Reference
from claimtrace.core.settings import get_settings
from claimtrace.ingest.text import find_arxiv, find_doi

NS = {"t": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


async def grobid_tei(pdf: bytes) -> str:
    """Raw TEI for a PDF. Raises RuntimeError on any failure (incl. a 200 without usable TEI)."""
    url = get_settings().grobid_url.rstrip("/") + "/api/processFulltextDocument"
    data = {"segmentSentences": "1", "includeRawCitations": "1"}
    try:
        async with make_client(timeout=180) as client:
            resp = await request(
                client,
                "POST",
                url,
                files={"input": ("doc.pdf", pdf, "application/pdf")},
                data=data,
            )
    except httpx.HTTPError as e:
        raise RuntimeError(f"GROBID unavailable: {type(e).__name__}") from e
    if resp.status_code != 200:
        raise RuntimeError(f"GROBID unavailable or failed: HTTP {resp.status_code}")
    try:  # a 200 can still be a proxy error page or an empty TEI: never an "empty paper"
        root = ET.fromstring(resp.text)
    except ET.ParseError as e:
        raise RuntimeError("GROBID failed: answer is not XML") from e
    if root.tag != f"{{{NS['t']}}}TEI":
        raise RuntimeError("GROBID failed: answer is not TEI")
    abstract = root.findall(".//t:teiHeader//t:abstract//t:p", NS)
    if not any(_para_text(p) for p in abstract + body_paragraphs(root)):
        raise RuntimeError("GROBID failed: no text extracted from the PDF")
    return resp.text


def _text(el: ET.Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def body_paragraphs(root: ET.Element) -> list[ET.Element]:
    body = root.find(".//t:text/t:body", NS)
    if body is None:
        return []
    return body.findall(".//t:p", NS) + body.findall(".//t:figure/t:figDesc", NS)


def _para_text(p: ET.Element) -> str:
    sents = p.findall("t:s", NS)
    return " ".join(t for s in sents if (t := _text(s))) if sents else _text(p)


def body_text(tei: str) -> str:
    """Title, abstract, then body paragraphs: sources' wrong-document guard looks for the
    title near the start of the text."""
    root = ET.fromstring(tei)
    title = root.find(".//t:teiHeader//t:titleStmt/t:title", NS)
    abstract = root.findall(".//t:teiHeader//t:abstract//t:p", NS)
    parts = [_text(title)] + [_para_text(p) for p in abstract + body_paragraphs(root)]
    return "\n\n".join(t for t in parts if t)


def _reference(bs: ET.Element) -> Reference:
    analytic, monogr = bs.find("t:analytic", NS), bs.find("t:monogr", NS)
    head = analytic if analytic is not None else monogr
    authors = []
    for pers in head.findall("t:author/t:persName", NS) if head is not None else []:
        sur = _text(pers.find("t:surname", NS))
        fore = " ".join(_text(f) for f in pers.findall("t:forename", NS))
        if sur:
            authors.append(f"{sur}, {fore}".strip(", "))
    title = _text(head.find("t:title", NS)) if head is not None else ""
    venue = _text(monogr.find("t:title", NS)) if analytic is not None and monogr is not None else ""
    when = bs.find(".//t:imprint/t:date", NS)
    year = re.match(r"\d{4}", (when.get("when") or _text(when)) if when is not None else "")
    ids = {(i.get("type") or "").lower(): _text(i) for i in bs.findall(".//t:idno", NS)}
    raw = _text(bs.find("t:note[@type='raw_reference']", NS))
    if not raw:  # handwritten / older TEI: rebuild a readable string from the fields
        raw = ". ".join(x for x in ["; ".join(authors), title, venue, year and year[0]] if x)
    return Reference(
        id=bs.get(XML_ID) or "",
        raw=raw,
        title=title or None,
        authors=authors,
        year=int(year[0]) if year else None,
        doi=find_doi(ids.get("doi", "")),
        arxiv_id=find_arxiv("arXiv:" + ids["arxiv"].removeprefix("arXiv:"))
        if ids.get("arxiv")
        else None,
        venue=venue or None,
    )


def parse_tei(tei: str) -> tuple[str | None, str, list[Reference], list[Citation], list[str]]:
    """TEI -> (title, body, references, citations, warnings)."""
    root = ET.fromstring(tei)
    title = _text(root.find(".//t:teiHeader//t:titleStmt/t:title", NS)) or None
    refs = [_reference(bs) for bs in root.findall(".//t:back//t:listBibl/t:biblStruct", NS)]
    refs = [r for r in refs if r.id]
    known = {r.id for r in refs}
    paras = body_paragraphs(root)
    cites, warnings = [], []
    for p in paras:
        for unit in p.findall("t:s", NS) or [p]:
            markers = unit.findall(".//t:ref[@type='bibr']", NS)
            if not markers:
                continue
            ids: list[str] = []
            unresolved = []
            for m in markers:
                target = (m.get("target") or "").lstrip("#")
                if target not in known:
                    unresolved.append(_text(m))
                elif target not in ids:
                    ids.append(target)
            cid = f"c{len(cites) + 1}"
            marker = " ".join(_text(m) for m in markers)
            cites.append(Citation(id=cid, sentence=_text(unit), ref_ids=ids, marker=marker))
            if unresolved:
                warnings.append(f"Citation {cid}: unresolved {', '.join(unresolved)}")
    body = "\n\n".join(t for p in paras if (t := _para_text(p)))
    return title, body, refs, cites, warnings
