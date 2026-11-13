import json
from pathlib import Path

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.core import llm
from claimtrace.core.schemas import InputFile, Reference
from claimtrace.ingest import parse_bib, parse_inputs, pdf_to_text, tei
from claimtrace.ingest.text import (
    find_arxiv,
    find_citations,
    find_doi,
    sentences,
    split_entries,
    split_manuscript,
    year_candidates,
)

FX = Path(__file__).parent / "fixtures" / "ingest"


def fx(name: str) -> InputFile:
    return InputFile(name=name, content=(FX / name).read_bytes())


def fake_llm(monkeypatch, *replies: str) -> None:
    model = FakeListChatModel(responses=list(replies))
    monkeypatch.setattr(llm, "get_model", lambda role: model)


def llm_down(monkeypatch) -> None:
    def boom(role):
        raise llm.LLMError("not configured")

    monkeypatch.setattr(llm, "get_model", boom)


# ---------- deterministic text helpers ----------


def test_split_at_last_reference_heading():
    text = (
        "Intro mentions References in passing.\n\nReferences\nnot this\n\n## 5. References\nA. 2020"
    )
    body, section, warn = split_manuscript(text)
    assert "not this" in body and section.strip() == "A. 2020" and warn is None
    assert split_manuscript("no refs here") == ("no refs here", None, None)


def test_split_entries_numbered_wrapped_and_unnumbered():
    sec = "1. Smith J. A title. J Biol\n12. 2020;3:1-5.\n2. Doe A. Other. 2019."
    assert split_entries(sec) == [
        ("1", "Smith J. A title. J Biol\n12. 2020;3:1-5."),
        ("2", "Doe A. Other. 2019."),
    ]
    assert [n for n, _ in split_entries("[1] A 2020\n[2] B 2021")] == ["1", "2"]
    assert split_entries("Doe A. X. 2020.\nRoe B. Y. 2021.") == [
        (None, "Doe A. X. 2020."),
        (None, "Roe B. Y. 2021."),
    ]
    blocks = split_entries("Doe A. X.\n  Nature 2020.\n\nRoe B. Y.\n  Cell 2021.")
    assert [raw for _, raw in blocks] == ["Doe A. X.\n  Nature 2020.", "Roe B. Y.\n  Cell 2021."]


def test_regex_fields():
    assert find_doi("see (doi:10.1000/ABC(12)3).") == "10.1000/abc(12)3"
    assert find_doi("https://doi.org/10.1038/nature12373.") == "10.1038/nature12373"
    assert find_arxiv("arXiv:2101.00001v3 [cs.CL]") == "2101.00001"
    assert find_arxiv("https://arxiv.org/abs/hep-th/9901001v1") == "hep-th/9901001"
    assert find_arxiv("doi:10.48550/arXiv.1706.03762") == "1706.03762"
    # years inside DOIs / arXiv ids are not publication years; parenthesised year wins
    assert year_candidates("Doe (2019b). X. doi:10.1038/s41586-020-2012-7") == [2019]
    assert year_candidates("arXiv:2001.12345 (preprint)") == []
    assert year_candidates("Smith J. The 1918 flu. Lancet (2020)")[0] == 2020


def test_sentences_survive_abbreviations():
    text = (
        "Smith et al. (2020) found X, e.g. in mice (Fig. 2). Values were 0.05 and 1.5 i.e. "
        "small. J. R. Smith agreed. Then Y [1].\n\n# Heading\nNext paragraph here."
    )
    assert sentences(text) == [
        "Smith et al. (2020) found X, e.g. in mice (Fig. 2).",
        "Values were 0.05 and 1.5 i.e. small.",
        "J. R. Smith agreed.",
        "Then Y [1].",
        "# Heading",
        "Next paragraph here.",
    ]


def test_citation_markers_and_resolution():
    refs = [
        Reference(id="1", raw="a"),
        Reference(id="2", raw="b"),
        Reference(id="3", raw="c"),
        Reference(id="vdb", raw="van der Berg, K. (2018). Title.", year=2018),
        Reference(id="key1", raw="x"),
    ]
    body = (
        "A holds [1, 3]. B holds [1-3]. C holds [2–3] and [9]. "
        "D holds (see van der Berg, 2018). E holds \\cite{key1,nokey}. "
        "F holds (n = 2020) and (in 2015) and (2010–2020)."
    )
    cites, warns = find_citations(body, refs)
    assert [(c.ref_ids, c.marker) for c in cites] == [
        (["1", "3"], "[1, 3]"),
        (["1", "2", "3"], "[1-3]"),
        (["2", "3"], "[2–3] [9]"),
        (["vdb"], "(see van der Berg, 2018)"),
        (["key1"], "\\cite{key1,nokey}"),
    ]
    assert warns == ["Citation c3: unresolved [9]", "Citation c5: unresolved nokey"]


def test_ambiguous_author_year_is_not_guessed():
    refs = [
        Reference(id="a", raw="Doe, A. (2019a). One.", year=2019),
        Reference(id="b", raw="Doe, A. (2019b). Two.", year=2019),
    ]
    cites, warns = find_citations("X holds (Doe, 2019). Y holds (Doe, 2019b).", refs)
    assert [c.ref_ids for c in cites] == [[], ["b"]]
    assert warns == ["Citation c1: unresolved Doe 2019"]


# ---------- pasted text: numeric, with LLM extraction + verbatim guard ----------


async def test_numeric_manuscript_with_guard(monkeypatch):
    reply = {
        "items": [
            {
                "index": 0,
                "title": "Statin therapy lowers LDL cholesterol in adults",
                "authors": ["Smith, J", "Doe, A"],
                "venue": "N Engl J Med",
                "year": 2019,
            },
            {  # hallucinated title + year + an invented author: all must be dropped
                "index": 1,
                "title": "Simvastatin improves survival in heart disease",
                "authors": ["Lee, K", "Park, S", "Kim, H"],
                "year": 1995,
            },
            {"index": 2, "title": "Lipid lowering without statins: a review", "authors": []},
        ]
    }
    fake_llm(monkeypatch, json.dumps(reply))
    ms = await parse_inputs((FX / "numeric.md").read_text(), [])
    assert ms.title == "Statins and cardiovascular outcomes"
    assert "[1] Smith J" not in ms.body and "## References" not in ms.body
    r1, r2, r3 = ms.references
    assert (r1.id, r1.title, r1.authors, r1.year, r1.doi, r1.venue) == (
        "1",
        "Statin therapy lowers LDL cholesterol in adults",
        ["Smith, J", "Doe, A"],
        2019,
        "10.1056/nejmoa1234567",
        "N Engl J Med",
    )
    assert r2.title is None and r2.year == 1994 and r2.authors == ["Lee, K", "Park, S"]
    assert r3.arxiv_id == "2101.00001" and r3.year == 2021
    assert any("Reference 2" in w and "title" in w and "year" in w for w in ms.warnings)
    assert [c.ref_ids for c in ms.citations] == [
        ["1"],
        ["2", "3"],
        ["1", "2", "3"],
        [],
        ["2", "3"],
    ]
    assert ms.citations[3].marker == "[7]"
    assert "Citation c4: unresolved [7]" in ms.warnings
    # every citing sentence is verbatim from the body
    assert all(c.sentence in ms.body for c in ms.citations)


async def test_llm_failure_keeps_regex_fields(monkeypatch):
    llm_down(monkeypatch)
    ms = await parse_inputs((FX / "numeric.md").read_text(), [])
    assert [r.title for r in ms.references] == [None, None, None]
    assert ms.references[0].doi == "10.1056/nejmoa1234567" and ms.references[1].year == 1994
    assert any("extraction failed" in w for w in ms.warnings)
    assert len(ms.citations) == 5


async def test_llm_invalid_json_twice_is_a_warning(monkeypatch):
    fake_llm(monkeypatch, "not json", "still not json")
    ms = await parse_inputs((FX / "numeric.md").read_text(), [])
    assert any("extraction failed" in w for w in ms.warnings)
    assert ms.references[2].arxiv_id == "2101.00001"


# ---------- author-year ----------


async def test_author_year_manuscript(monkeypatch):
    llm_down(monkeypatch)  # resolution must work from the raw strings alone
    ms = await parse_inputs(None, [fx("author_year.txt")])
    ids = {r.raw.split(".")[0][:12]: r.id for r in ms.references}
    assert len(ms.references) == 5 and ids["Smith, J"] == "r5"
    got = [(c.ref_ids, c.marker) for c in ms.citations]
    assert got == [
        (["r5", "r1"], "(Smith et al., 2020; Doe & Roe, 2019a)"),
        (["r2"], "Doe and Roe (2019b)"),
        (["r4", "r3"], "Müller (2018) García et al. (2021)"),
        ([], "(e.g., Walker, 2017)"),
    ]
    assert "Citation c4: unresolved Walker 2017" in ms.warnings
    assert all("2015" not in c.sentence for c in ms.citations)


# ---------- .bib ----------


def test_parse_bib():
    smith, brown = parse_bib((FX / "refs.bib").read_text())
    assert smith.id == "smith2020" and smith.title == "Sleep consolidates declarative memory"
    assert smith.authors == ["Smith, John", "Jones, Kate"]
    assert smith.doi == "10.1038/s41593-020-0001-1" and smith.year == 2020
    assert smith.raw.startswith("@article{smith2020,") and smith.raw.endswith("}")
    assert brown.authors == ["Bröwn, Tom"] and brown.arxiv_id == "2101.00001"


async def test_text_plus_bib(monkeypatch):
    llm_down(monkeypatch)
    text = "Sleep helps (Smith et al., 2020). Statins too \\cite{brown2021}.\n\nReferences\n[1] X"
    ms = await parse_inputs(text, [fx("refs.bib"), InputFile(name="notes.docx", content=b"x")])
    assert [r.id for r in ms.references] == ["smith2020", "brown2021"]
    assert [c.ref_ids for c in ms.citations] == [["smith2020"], ["brown2021"]]
    assert any("notes.docx" in w for w in ms.warnings)
    assert any("using the .bib" in w for w in ms.warnings)


# ---------- GROBID / TEI ----------


def grobid(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        tei,
        "make_client",
        lambda timeout=30: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_parse_tei():
    title, body, refs, cites, warns = tei.parse_tei((FX / "fulltext.tei.xml").read_text())
    assert title == "Statins in practice"
    assert body.startswith("Statins lower LDL cholesterol [1]. This sentence cites nothing.")
    assert "We review statin trials" not in body  # abstract is not body
    b0, b1 = refs
    assert (b0.id, b0.doi, b0.year, b0.venue) == (
        "b0",
        "10.1056/nejmoa1234567",
        2019,
        "N Engl J Med",
    )
    assert b0.authors == ["Smith, John", "Doe, Anna"] and b0.raw.startswith("Smith J, Doe A.")
    assert b1.arxiv_id == "2101.00001" and b1.year == 2021
    assert [(c.sentence, c.ref_ids) for c in cites] == [
        ("Statins lower LDL cholesterol [1].", ["b0"]),
        ("Mortality fell in two trials [1][2] but not in a third [9].", ["b0", "b1"]),
    ]
    assert warns == ["Citation c2: unresolved [9]"]


async def test_pdf_to_text_and_parse_inputs_pdf(monkeypatch):
    tei_xml = (FX / "fulltext.tei.xml").read_text()
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["body"] = req.read()
        return httpx.Response(200, text=tei_xml)

    grobid(monkeypatch, handler)
    text = await pdf_to_text(b"%PDF-1.4 fake")
    assert seen["url"].endswith("/api/processFulltextDocument") and b"%PDF-1.4" in seen["body"]
    # title, author surnames, abstract first (sources' wrong-document guard), then body
    assert text.split("\n\n") == [
        "Statins in practice",
        "Garcia Wei",
        "We review statin trials.",
        "Statins lower LDL cholesterol [1]. This sentence cites nothing. "
        "Mortality fell in two trials [1][2] but not in a third [9].",
        "Figure 1. LDL over time.",
    ]
    ms = await parse_inputs(None, [InputFile(name="paper.PDF", content=b"%PDF")])
    assert ms.title == "Statins in practice" and [r.id for r in ms.references] == ["b0", "b1"]
    assert [c.ref_ids for c in ms.citations] == [["b0"], ["b0", "b1"]]


async def test_grobid_down(monkeypatch):
    def handler(req):
        raise httpx.ConnectError("refused")

    grobid(monkeypatch, handler)
    monkeypatch.setattr("claimtrace.core.http.asyncio.sleep", _no_sleep)
    with pytest.raises(RuntimeError, match="GROBID unavailable"):
        await pdf_to_text(b"%PDF")
    ms = await parse_inputs(None, [InputFile(name="p.pdf", content=b"%PDF")])
    assert ms.citations == [] and any("GROBID unavailable" in w for w in ms.warnings)


async def test_grobid_error_status(monkeypatch):
    grobid(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    monkeypatch.setattr("claimtrace.core.http.asyncio.sleep", _no_sleep)
    with pytest.raises(RuntimeError, match="HTTP 500"):
        await pdf_to_text(b"%PDF")


async def _no_sleep(_):
    return None


async def test_no_input():
    ms = await parse_inputs("   ", [])
    assert ms.references == [] and ms.warnings == ["No input to analyse"]


# ---------- regressions around the adversarial fixes ----------


def test_fixes_do_not_over_reach():
    from claimtrace.ingest.extract import _Item, apply_guarded, regex_reference

    smith = Reference(
        id="s", raw="Smith, J. (2020). One. J Biol.", authors=["Smith, J."], year=2020
    )
    # a comma before a single narrative author is not an author list
    (c,), warns = find_citations("However, Smith (2020) showed X.", [smith])
    assert c.ref_ids == ["s"] and not warns
    # the 3-author narrative is unresolved with a warning, never silently dropped
    (c,), warns = find_citations("Jones, Smith and Lee (2020) showed X.", [smith])
    assert c.ref_ids == [] and warns
    # page locators are not year suffixes
    (c,), _ = find_citations("Shown (Smith, 2020, p 5).", [smith])
    assert c.ref_ids == ["s"]
    # wrapped lowercase lines join; a particle-led name still starts an entry
    assert len(split_entries("de Souza, A. (2019). X.\nvan Dijk, B. (2020). Y.")) == 2
    # title ending in '?' and IEEE quoted title still pass the guard; given names in raw pass
    r = regex_reference("1", 'J. Smith, "Does aspirin work?" in Proc. X, 2020.')
    apply_guarded(r, _Item(index=0, title="Does aspirin work?"), [])
    r2 = regex_reference("2", 'Smith, John, "Aspirin and mortality," in Proc. X, 2020.')
    apply_guarded(r2, _Item(index=0, title="Aspirin and mortality", authors=["Smith, John"]), [])
    assert (r.title, r2.title, r2.authors) == (
        "Does aspirin work?",
        "Aspirin and mortality",
        ["Smith, John"],
    )
    # year ranges never count; parenthesised title year loses to a Vancouver imprint
    assert year_candidates("Doe A. Trends 1990-2016. BMJ. 2018;1:1.") == [2018]
