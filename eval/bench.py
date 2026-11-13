"""Bench documents for `--suite timing`, fetched at runtime into eval/.cache/bench/ (gitignored,
never committed). `load(name)` -> (text, files) ready for `pipeline.run`."""

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

import httpx

from claimtrace.core.schemas import InputFile
from claimtrace.core.settings import get_settings

BENCH = Path(__file__).parent / ".cache" / "bench"
ARXIV = "1706.03762v7"  # Attention Is All You Need
REVIEW_PMCID = "PMC9820749"  # IJMS 24:700 review on TRP channels, CC BY 4.0, 204 references
EPMC_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{}/fullTextXML"
DOCS = ("attention-pdf", "attention-text", "review-200")


class Skip(Exception):
    """A bench document that cannot run here (e.g. GROBID unreachable)."""


def _get(url: str, name: str) -> bytes:
    path = BENCH / name
    if not path.exists():
        BENCH.mkdir(parents=True, exist_ok=True)
        r = httpx.get(url, follow_redirects=True, timeout=120)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path.read_bytes()


def _paragraphs(text: str) -> str:
    paras = (" ".join(p.split()) for p in re.split(r"\n\s*\n", text))
    return "\n\n".join(p for p in paras if p)


# ---------------------------------------------------------------- arXiv HTML (LaTeXML)

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}
_SKIP = {"table", "figure", "script", "style", "nav", "header", "footer", "button", "math"}
_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "section", "figcaption"}


class _HtmlText(HTMLParser):
    """Text of <article> from its <h1> to the end of the bibliography: blocks become paragraphs,
    math becomes its LaTeX alttext, tables / figures / footnotes are dropped. Assumes
    well-formed (LaTeXML) markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[bool, str | None]] = []  # (skips, text at the end tag)
        self.skip, self.on = 0, False

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            return
        a = dict(attrs)
        cls = a.get("class") or ""
        self.on = self.on or tag == "h1"  # from the title (skips the licence notice above it)
        skip = tag in _SKIP or "ltx_note" in cls
        if self.on and not self.skip:
            if tag == "math":
                self.out.append(a.get("alttext") or "")
            elif tag in _BLOCK:
                self.out.append("\n\n")
        end = "\n\n" if tag in _BLOCK else " " if re.search(r"ltx_(tag|bibblock)\b", cls) else ""
        if "ltx_bibliography" in cls:
            end = None  # stop after the references (the appendix is figures only)
        self.stack.append((skip, end))
        self.skip += skip

    def handle_endtag(self, tag):
        if tag in _VOID or not self.stack:
            return
        skip, end = self.stack.pop()
        self.skip -= skip
        if self.on and not self.skip:
            self.out.append(end or "")
        if end is None or tag == "article":
            self.on = False

    def handle_data(self, data):
        if self.on and not self.skip:
            self.out.append(re.sub(r"\s+", " ", data))  # HTML whitespace; blocks add breaks


def html_to_text(html: str) -> str:
    p = _HtmlText()
    p.feed(html)
    return _paragraphs("".join(p.out))


# ---------------------------------------------------------------- Europe PMC JATS

_JATS_DROP = {"fig", "table-wrap", "table", "disp-formula", "supplementary-material"}


def jats_to_text(xml: bytes) -> str:
    """Title, abstract and body paragraphs (citations as they appear, e.g. `[1,2]`), then a
    `References` heading and `[n] citation` entries numbered by their label."""
    root = ET.fromstring(xml)
    for parent in root.iter():
        for child in [c for c in parent if c.tag in _JATS_DROP]:
            parent.remove(child)
    title = root.find(".//article-title")
    blocks = ["".join(title.itertext()) if title is not None else ""]

    def walk(el):
        if el.tag in ("title", "p"):  # whole paragraph, nested <p> included once
            blocks.append("".join(el.itertext()))
            return
        for c in el:
            walk(c)

    for part in (root.find(".//abstract"), root.find(".//body")):
        if part is not None:
            walk(part)
    blocks.append("References")
    for i, ref in enumerate(root.iterfind(".//ref-list/ref"), 1):
        cit = ref.find("mixed-citation")
        cit = cit if cit is not None else ref.find("element-citation")
        num = re.sub(r"\D", "", ref.findtext("label") or "") or str(i)
        blocks.append(f"[{num}] " + "".join(cit.itertext() if cit is not None else []))
    return _paragraphs("\n\n".join(blocks))


# ---------------------------------------------------------------- loader


def grobid_alive() -> bool:
    try:
        return httpx.get(f"{get_settings().grobid_url}/api/isalive", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


def load(name: str) -> tuple[str | None, list[InputFile]]:
    if name == "attention-pdf":
        if not grobid_alive():
            raise Skip(f"GROBID not reachable at GROBID_URL ({get_settings().grobid_url})")
        pdf = _get(f"https://arxiv.org/pdf/{ARXIV}", "attention.pdf")
        return None, [InputFile(name="attention.pdf", content=pdf)]
    if name == "attention-text":
        html = _get(f"https://arxiv.org/html/{ARXIV}", "attention.html")
        return html_to_text(html.decode()), []
    if name == "review-200":
        return jats_to_text(_get(EPMC_XML.format(REVIEW_PMCID), f"{REVIEW_PMCID}.xml")), []
    raise ValueError(f"unknown bench document {name!r}; known: {', '.join(DOCS)}")
