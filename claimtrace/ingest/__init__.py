"""Ingest: manuscript text / .bib / PDF -> Manuscript. Owner: T1 (docs/TASKS.md)."""

import asyncio
import re
import threading
from collections import Counter

import bibtexparser
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from bibtexparser.bparser import BibTexParser
from bibtexparser.customization import convert_to_unicode

from claimtrace.core.schemas import InputFile, Manuscript, Reference
from claimtrace.core.settings import get_settings
from claimtrace.ingest.extract import extract_references
from claimtrace.ingest.tei import body_text, grobid_tei, parse_tei
from claimtrace.ingest.text import (
    find_arxiv,
    find_citations,
    find_doi,
    guess_title,
    split_entries,
    split_manuscript,
    year_candidates,
)

TEXT_EXT = (".txt", ".md")


def _bib_raw(src: str, key: str) -> str | None:
    """The verbatim `@type{key, ...}` / `@type(key, ...)` entry, brace-matched from the entry's
    opening delimiter (escaped `\\{` / `\\}` skipped), or None."""
    m = re.search(rf"@\w+\s*([{{(])\s*{re.escape(key)}\s*,", src)
    if not m:
        return None
    close = "}" if m.group(1) == "{" else ")"
    depth, i = 0, m.start(1)
    while i < len(src):
        c = src[i]
        if c == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 0 and c == close:
            return src[m.start() : i + 1]
        i += 1
    return None


def parse_bib(src: str) -> list[Reference]:
    parser = BibTexParser(common_strings=True)
    parser.customization = convert_to_unicode
    refs = []
    for e in bibtexparser.loads(src, parser=parser).entries:
        raw = _bib_raw(src, e["ID"]) or ""
        doi_field = e.get("doi", "")
        eprint = e.get("eprint", "")
        arxiv = None
        if eprint and "arxiv" in (e.get("archiveprefix", "") + e.get("eprinttype", "")).lower():
            arxiv = find_arxiv("arXiv:" + eprint)
        years = year_candidates(e.get("year", "") or e.get("date", ""))
        refs.append(
            Reference(
                id=e["ID"],
                raw=raw or str({k: v for k, v in e.items() if k != "ENTRYTYPE"}),
                title=e.get("title") or None,
                authors=[a.strip() for a in re.split(r"\s+and\s+", e.get("author", "")) if a],
                year=years[0] if years else None,
                doi=find_doi(doi_field) if doi_field else None,
                arxiv_id=arxiv or find_arxiv(" ".join(e.get(k, "") for k in ("journal", "url"))),
                venue=e.get("journal") or e.get("booktitle") or e.get("publisher") or None,
            )
        )
    return refs


def _lf(s: str) -> str:
    """Browsers submit textarea text with CRLF and Windows files use it too; every line-based
    rule below (reference heading, entry prefixes) expects plain LF."""
    return s.replace("\r\n", "\n").replace("\r", "\n")


async def parse_inputs(text: str | None, files: list[InputFile]) -> Manuscript:
    """Pasted text and/or files -> Manuscript. The first manuscript source wins (pasted text,
    then .txt/.md, then .pdf); .bib files supply the references when present. Per-item problems
    (GROBID down, unknown extension, LLM failure) become warnings, never exceptions."""
    warnings: list[str] = []
    manuscripts: list[tuple[str, str | bytes]] = []  # (kind, content)
    bib_refs: list[Reference] = []
    if text and text.strip():
        manuscripts.append(("text", _lf(text)))
    for f in files:
        ext = "." + f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
        if ext in TEXT_EXT:
            manuscripts.append(("text", _lf(f.content.decode("utf-8-sig", errors="replace"))))
        elif ext == ".pdf":
            manuscripts.append(("pdf", f.content))
        elif ext == ".bib":
            try:
                new = parse_bib(f.content.decode("utf-8-sig", errors="replace"))
            except Exception as e:
                warnings.append(f"{f.name}: could not parse BibTeX ({type(e).__name__})")
                continue
            seen = {r.id for r in bib_refs}
            for r in new:
                if r.id in seen:
                    warnings.append(f"{f.name}: duplicate BibTeX key {r.id} ignored")
                else:
                    seen.add(r.id)
                    bib_refs.append(r)
        else:
            warnings.append(f"{f.name}: unsupported file type, ignored")
    if len(manuscripts) > 1:
        warnings.append(
            f"{len(manuscripts) - 1} extra manuscript input(s) ignored; using the first"
        )

    ms = Manuscript(warnings=warnings)
    if manuscripts:
        kind, content = manuscripts[0]
        if kind == "pdf":
            await _from_pdf(ms, content, bib_refs)
        else:
            await _from_text(ms, content, bib_refs)
    elif bib_refs:
        ms.references = bib_refs
        ms.warnings.append("No manuscript text: references audited, no citations checked")
    else:
        ms.warnings.append("No input to analyse")
    return ms


async def _from_text(ms: Manuscript, text: str, bib_refs: list[Reference]) -> None:
    body, section, warn = split_manuscript(text)
    ms.title, ms.body = guess_title(body), body
    if warn:
        ms.warnings.append(warn)
    if bib_refs:
        ms.references = bib_refs
        if section:
            ms.warnings.append("Reference section in the text ignored; using the .bib file")
    elif section:
        entries, limit = split_entries(section), get_settings().max_references
        if len(entries) > limit:  # before the LLM: 10 000 entries must not mean 500 batches
            ms.warnings.append(f"{len(entries) - limit} references skipped (limit {limit})")
            entries = entries[:limit]
        pairs = [(num or f"r{i + 1}", raw) for i, (num, raw) in enumerate(entries)]
        ms.references, warns = await extract_references(pairs)
        ms.warnings += warns
    else:
        ms.warnings.append("No reference section found (expected a 'References' heading)")
    ms.citations, warns = find_citations(body, ms.references)
    ms.warnings += warns


async def _from_pdf(ms: Manuscript, pdf: bytes, bib_refs: list[Reference]) -> None:
    try:
        tei = await grobid_tei(pdf)
        ms.title, ms.body, refs, cites, warns = parse_tei(tei)
    except Exception as e:
        ms.warnings.append(f"Could not read the PDF: {e}")
        ms.references = bib_refs
        return
    if bib_refs:  # GROBID ids (b0, b1, ...) mean nothing to a .bib: re-resolve from the body
        ms.references = bib_refs
        ms.citations, warns = find_citations(ms.body, bib_refs)
    else:
        ms.references, ms.citations = refs, cites
    ms.warnings += warns


async def pdf_to_text(pdf: bytes) -> str:
    """PDF -> plain body text via GROBID. Injected into sources by the pipeline.
    Raises RuntimeError when GROBID cannot be reached or returns no usable TEI."""
    return body_text(await grobid_tei(pdf))


# pdfium is not thread-safe: one document at a time across all threads.
# ponytail: global lock, a process pool if source PDFs become the bottleneck.
_PDFIUM_LOCK = threading.Lock()
EDGE_LINES = 3
MAX_TITLE_LINE = 300  # header/footer candidates: first and last lines of each page


def _page_text(tp) -> str:
    # pdfium writes a hyphen at a line end as U+FFFE: put the printed hyphen back
    return tp.get_text_range().replace("\r\n", "\n").replace("\r", "\n").replace("\ufffe", "-")


def _big_lines(tp) -> list[str]:
    """Page-one lines set in the largest upright font, first run only: usually the title.
    Rotated text (arXiv's side stamp) is ignored."""
    text = tp.get_text_range()  # one char per pdfium char index (checked below)
    if len(text) != tp.count_chars():
        return []
    lines: list[tuple[float, str]] = []
    size, rotated, start = 0.0, False, 0
    for i, ch in enumerate(text + "\n"):
        if ch in "\r\n":
            line = text[start:i].strip().replace("\ufffe", "-")
            if not rotated and sum(c.isalpha() for c in line) >= 3:
                lines.append((round(size, 1), line))
            size, rotated, start = 0.0, False, i + 1
        elif not ch.isspace():
            size = max(size, pdfium_c.FPDFText_GetFontSize(tp.raw, i))
            rotated = rotated or abs(pdfium_c.FPDFText_GetCharAngle(tp.raw, i)) > 0.01
    top = max((sz for sz, _ in lines), default=None)
    run: list[str] = []
    for sz, line in lines:
        if sz == top:
            run.append(line)
        elif run:
            break
    return run


def _pdf_pages(pdf: bytes) -> tuple[str, list[str], list[str]]:
    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf)  # raises PdfiumError on broken / password-protected PDFs
        try:
            title = (doc.get_metadata_dict().get("Title") or "").strip()
            pages, big = [], []
            for i in range(len(doc)):
                page = doc[i]
                tp = page.get_textpage()
                pages.append(_page_text(tp))
                if i == 0:
                    big = _big_lines(tp)
                tp.close()
                page.close()
        finally:
            doc.close()
    return title, big, pages


def _fast_text(title: str, big: list[str], pages: list[str]) -> str:
    lines = [[ln.strip() for ln in p.split("\n") if ln.strip()] for p in pages]
    drop: set[str] = set()
    if len(pages) >= 3:  # a line repeated at the top/bottom of >= half the pages is a running head
        edges = Counter(ln for ls in lines for ln in set(ls[:EDGE_LINES] + ls[-EDGE_LINES:]))
        drop = {ln for ln, n in edges.items() if 2 * n >= len(pages)}
    body = "\n\n".join("\n".join(ln for ln in ls if ln not in drop) for ls in lines).strip()
    if not body:
        raise ValueError("PDF has no extractable text")
    # Title line first (the wrong-document guard reads line one): the metadata title when page
    # one really carries it ("Microsoft Word - x.doc" never does), else page one's largest type.
    first = " ".join(" ".join(lines[0]).split()).casefold() if lines else ""
    title = " ".join(title.split())
    if not (title and title.casefold() in first):
        title = " ".join(" ".join(big).split())
    return f"{title}\n\n{body}" if title and len(title) <= MAX_TITLE_LINE else body


async def pdf_to_text_fast(pdf: bytes) -> str:
    """PDF -> plain text with pdfium, no GROBID (for source papers, not the manuscript).
    Text stays as pdfium gives it (no de-hyphenation), minus running headers/footers; the
    metadata title goes first when page one carries it, else the text starts with its own
    first line. Caveat: pdfium flattens super/subscripts ("10^5" reads "105"); no markers are
    invented. Raises on unreadable, encrypted or text-less (scanned) PDFs."""
    return _fast_text(*await asyncio.to_thread(_pdf_pages, pdf))
