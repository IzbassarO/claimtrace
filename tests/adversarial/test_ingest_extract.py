"""Adversarial: reference fields that pass the guards but are wrong."""

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.ingest.extract import _Item, apply_guarded, extract_references, regex_reference


@pytest.mark.parametrize(
    "raw",
    [
        "Smith J. Global burden of disease 1990-2016. Lancet. 2017;390:1211-59.",
        "Smith J. Lessons from the H1N1 pandemic (2009). Lancet. 2017;390:1211-59.",
    ],
)
async def test_year_not_taken_from_title_when_llm_down(raw):
    (r,), warnings = await extract_references(
        [("1", raw)], model=FakeListChatModel(responses=["not json"] * 4)
    )
    assert warnings  # the LLM path really failed
    assert r.year in (2017, None)  # a title year would be reported as a 2+ year mismatch


def test_truncated_title_is_not_accepted():
    r = regex_reference("1", "Smith A. Aspirin does not reduce mortality. Lancet. 2020;1:1.")
    apply_guarded(r, _Item(index=0, title="Aspirin does"), [])
    assert r.title in (None, "Aspirin does not reduce mortality")


def test_fabricated_given_name_is_dropped():
    r = regex_reference("1", "Smith A. Aspirin and mortality. Lancet. 2020;1:1.")
    warnings: list[str] = []
    apply_guarded(r, _Item(index=0, authors=["Smith, Jonathan"]), warnings)
    assert "Jonathan" not in " ".join(r.authors)
