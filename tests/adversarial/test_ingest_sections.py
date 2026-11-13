"""Adversarial: reference section boundaries and entry splitting."""

import pytest

from claimtrace.core import llm
from claimtrace.ingest import parse_inputs
from claimtrace.ingest.text import split_entries


@pytest.fixture(autouse=True)
def llm_down(monkeypatch):
    def boom(role):
        raise llm.LLMError("not configured")

    monkeypatch.setattr(llm, "get_model", boom)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: a supplement's own 'References' list replaces the main one; [1] -> wrong paper",
)
async def test_main_citations_not_resolved_against_supplement_references():
    text = (
        "Aspirin lowers mortality [1].\n\nReferences\n\n"
        "1. Doe A. Aspirin and mortality. Lancet. 2019;1:1.\n\n"
        "Supplementary Information\n\nStatins were also examined [1].\n\nReferences\n\n"
        "1. Roe B. Statin trials. BMJ. 2020;2:2.\n"
    )
    ms = await parse_inputs(text, [])
    refs = {r.id: r for r in ms.references}
    aspirin = next(c for c in ms.citations if c.sentence.startswith("Aspirin"))
    assert all("Roe" not in refs[i].raw for i in aspirin.ref_ids)


@pytest.mark.xfail(
    strict=True,
    reason="BUG: appendix after the reference list is glued into the last reference, "
    "its citations never checked",
)
async def test_appendix_after_references_is_not_a_reference():
    text = (
        "Aspirin lowers mortality [1].\n\nReferences\n\n"
        "[1] Doe A. Aspirin and mortality. Lancet. 2019;1:1.\n"
        "[2] Roe B. Statin trials. BMJ. 2020;2:2.\n\n"
        "Appendix A. Proofs\n\nThe bound holds as shown in [2].\n"
    )
    ms = await parse_inputs(text, [])
    assert all("bound holds" not in r.raw for r in ms.references)
    assert any(c.ref_ids == ["2"] for c in ms.citations)


@pytest.mark.xfail(
    strict=True, reason="BUG: hard-wrapped unnumbered reference list becomes one ref per line"
)
def test_wrapped_unnumbered_entries_are_joined():
    section = (
        "Doe, A. (2019). A long title that\n"
        "wraps onto the next line. J Biol.\n"
        "Roe, B. (2020). Statin trials. J Chem."
    )
    entries = [raw for _, raw in split_entries(section)]
    assert len(entries) == 2 and entries[0].startswith("Doe") and entries[1].startswith("Roe")
