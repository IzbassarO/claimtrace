"""The built-in sample (claimtrace/web/static/sample.txt, served as /static/sample.txt and baked
into the image) must parse into its 3 references and 3 citing sentences, offline, however the
browser encodes line breaks."""

from pathlib import Path

import pytest

from claimtrace.core import llm
from claimtrace.ingest import parse_inputs
from claimtrace.ingest.text import split_manuscript

SAMPLE = Path(__file__).resolve().parent.parent / "claimtrace/web/static/sample.txt"


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    def down(role):
        raise llm.LLMError("offline test")

    monkeypatch.setattr(llm, "get_model", down)


@pytest.mark.parametrize("eol", ["\n", "\r\n", "\r"])
async def test_sample_parses_with_any_line_endings(eol):
    text = SAMPLE.read_text().replace("\n", eol)
    ms = await parse_inputs(text, [])
    assert [r.id for r in ms.references] == ["1", "2", "3"]
    assert len(ms.citations) == 3
    assert [c.ref_ids for c in ms.citations] == [["1"], ["2"], ["3"]]
    assert not any("No reference section" in w for w in ms.warnings)
    assert not any("unresolved" in w for w in ms.warnings)


@pytest.mark.parametrize(
    "heading",
    [
        "References",
        "REFERENCES",
        "references",
        "7 References",
        "7. References",
        "VII. REFERENCES",
        "7.1 References",
        "## References",
        "**References**",
        "References:",
        "References and Notes",
        "Reference list",
        "Bibliography",
        "BIBLIOGRAPHY",
        "Literature Cited",
        "Cited literature",
        "Works Cited",
    ],
)
def test_reference_heading_variants(heading):
    text = f"Body cites [1].\n\n{heading}\n\n[1] Doe J. A paper. 2020.\n"
    body, section, warning = split_manuscript(text)
    assert section and "Doe J." in section and "Doe J." not in body, heading
