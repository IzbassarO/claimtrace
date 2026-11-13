"""Regression tests for review findings: title start guard, TEI sup/sub, .bib raw, early cap."""

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import claimtrace.ingest as ingest
from claimtrace.core import llm
from claimtrace.core.schemas import InputFile
from claimtrace.core.settings import Settings
from claimtrace.ingest import _bib_raw, parse_bib, parse_inputs, tei
from claimtrace.ingest.extract import _title_in_raw

# ---------- 1. title must start where the title field starts ----------


@pytest.mark.parametrize(
    "title, raw, ok",
    [
        ("aspirin reduces mortality in older adults", "Smith J. (2020). No aspirin reduces "
         "mortality in older adults.", False),
        ("No aspirin reduces mortality in older adults", "Smith J. (2020). No aspirin reduces "
         "mortality in older adults.", True),
        ("aspirin reduces mortality", "Smith J. Not. Aspirin reduces mortality. Lancet.", False),
        ("aspirin reduces mortality", "Smith J. Non-aspirin reduces mortality. Lancet.", False),
        ("reduces mortality", "Smith J. Aspirin reduces mortality. Lancet. 2019", False),
        ("Statin therapy", "Smith J, Doe A. Statin therapy. N Engl J Med. 2019", True),
        ("Statin therapy", "J. Smith, “Statin therapy,” in Proc. X, 2019", True),
        ("Statin therapy", "Smith, J. 2019, Statin therapy. Lancet", True),
        ("Statin therapy", "Smith J (2019) Statin therapy. Lancet", True),
        ("Statin therapy", "Statin therapy. Lancet. 2019", True),
    ],
)  # fmt: skip
def test_title_start_boundary_and_negation(title, raw, ok):
    assert _title_in_raw(title, raw) is ok


async def test_dropped_leading_negation_title_is_a_warning(monkeypatch):
    raw = "Smith J. (2020). No aspirin reduces mortality in older adults. Lancet."
    reply = {"items": [{"index": 0, "title": "aspirin reduces mortality in older adults"}]}
    model = FakeListChatModel(responses=[json.dumps(reply)])
    monkeypatch.setattr(llm, "get_model", lambda role: model)
    ms = await parse_inputs(f"Aspirin helps [1].\n\nReferences\n[1] {raw}", [])
    assert ms.references[0].title is None
    assert any("Reference 1" in w and "title" in w for w in ms.warnings)


# ---------- 2. TEI superscript / subscript never merge into digits ----------

TEI = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt>
<title>CO<hi rend="subscript">2</hi> uptake</title></titleStmt><sourceDesc><biblStruct/>
</sourceDesc></fileDesc><profileDesc><abstract><p>About 10<hi rend="superscript">-3</hi> M.</p>
</abstract></profileDesc></teiHeader><text><body><div>
<p>We measured 10<hi rend="superscript">5</hi> cells in H<hi rend="subscript">2</hi>O.</p>
<p><s>Plain 2<hi rend="italic">x</hi> text.</s></p>
</div></body></text></TEI>"""


def test_tei_sup_sub_marked():
    text = tei.body_text(TEI)
    assert "We measured 10^5 cells in H_2O." in text and "105" not in text
    assert text.startswith("CO_2 uptake") and "About 10^-3 M." in text
    assert "Plain 2x text." in text
    title, body, *_ = tei.parse_tei(TEI)
    assert title == "CO_2 uptake" and "10^5 cells" in body


# ---------- 3. .bib raw is the whole entry ----------


@pytest.mark.parametrize(
    "entry",
    [
        "@article{k, title={Study}, author={Smith, John}, year={2020a}}",
        "@article{k,\n  title = {A {Nested} Study},\n  year = 2020\n}",
        '@article{k, title = "Quoted {B}rackets", note = {50\\% \\{odd}, year={2020}}',
        "@article(k, title={Paren entry}, year={2020})",
        "@ARTICLE { k , title={Spaced}, year={2020}}",
    ],
)
def test_bib_raw_is_full_entry(entry):
    src = f"% comment\n@misc{{other, title={{Other}}}}\n\n{entry}\n\n@misc{{z, title={{Z}}}}\n"
    assert _bib_raw(src, "k") == entry


async def test_bib_author_year_suffix_resolves(monkeypatch):
    bib = (
        "@article{s20a, title={Study one}, author={Smith, John}, year={2020a}}\n"
        "@article{s20b, title={Study two}, author={Smith, John}, year={2020b}}\n"
    )
    ms = await parse_inputs(
        "Sleep helps (Smith, 2020a). It also hurts (Smith, 2020b).",
        [InputFile(name="r.bib", content=bib.encode())],
    )
    assert parse_bib(bib)[0].raw.endswith("year={2020a}}")
    assert [c.ref_ids for c in ms.citations] == [["s20a"], ["s20b"]]


# ---------- 4. MAX_REFERENCES applied before LLM extraction ----------


async def test_max_references_before_extraction(monkeypatch):
    calls = []

    def boom(role):
        calls.append(role)
        raise llm.LLMError("not configured")

    monkeypatch.setattr(llm, "get_model", boom)
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(_env_file=None, max_references=3))
    refs = "\n".join(f"[{i}] Author{i} A. Title {i}. J. 2020." for i in range(1, 51))
    ms = await parse_inputs(f"Text [1] and [5].\n\nReferences\n{refs}", [])
    assert [r.id for r in ms.references] == ["1", "2", "3"]
    assert "47 references skipped (limit 3)" in ms.warnings
    assert len(calls) == 1  # one extraction batch, not three
    assert "Citation c1: unresolved [5]" in ms.warnings
