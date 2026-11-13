"""Streaming reference callbacks: same Manuscript, every reference once, small first chunk."""

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from claimtrace.core import llm
from claimtrace.core.schemas import InputFile
from claimtrace.ingest import parse_inputs, tei
from claimtrace.ingest.text import split_entries, split_manuscript

FX = Path(__file__).parent / "fixtures" / "ingest"
LONG = ["numbered_long.md", "unnumbered_long.txt"]


class EchoModel(BaseChatModel):
    """Deterministic per entry: title = the entry's second '. ' field. Sleeps `delay` per entry,
    so a 5-entry chunk answers before a 20-entry one."""

    delay: float = 0.0

    @property
    def _llm_type(self) -> str:
        return "echo"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw):
        lines = [ln for ln in messages[-1].content.splitlines() if ln.startswith('{"index"')]
        items = [json.loads(ln) for ln in lines]
        await asyncio.sleep(self.delay * len(items))
        out = []
        for it in items:
            parts = it["text"].replace("\n", " ").split(". ")
            out.append({"index": it["index"], "title": parts[1] if len(parts) > 1 else None})
        msg = AIMessage(json.dumps({"items": out}))
        return ChatResult(generations=[ChatGeneration(message=msg)])


def echo(monkeypatch, delay: float = 0.0) -> None:
    model = EchoModel(delay=delay)
    monkeypatch.setattr(llm, "get_model", lambda role: model)


def grobid_ok(monkeypatch) -> None:
    xml = (FX / "fulltext.tei.xml").read_text()
    monkeypatch.setattr(
        tei,
        "make_grobid_client",
        lambda timeout=180: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, text=xml))
        ),
    )


def inputs(name: str) -> tuple[str | None, list[InputFile]]:
    if name == "paper.pdf":
        return None, [InputFile(name=name, content=b"%PDF")]
    if name == "text+bib":
        return (FX / "numeric.md").read_text(), [
            InputFile(name="refs.bib", content=(FX / "refs.bib").read_bytes())
        ]
    return None, [InputFile(name=name, content=(FX / name).read_bytes())]


CASES = ["numeric.md", "author_year.txt", "refs.bib", "paper.pdf", "text+bib", *LONG]


@pytest.mark.parametrize("name", CASES)
async def test_streaming_does_not_change_the_manuscript(monkeypatch, name):
    echo(monkeypatch)
    grobid_ok(monkeypatch)
    plain = await parse_inputs(*inputs(name))
    got, doi = [], []
    streamed = await parse_inputs(*inputs(name), on_references=got.append, on_doi_refs=doi.append)
    assert streamed.model_dump() == plain.model_dump()
    assert plain.references
    flat = [r for chunk in got for r in chunk]
    # every final reference exactly once, as the very objects in the Manuscript
    assert sorted(map(id, flat)) == sorted(map(id, streamed.references))
    assert len(doi) <= 1


@pytest.mark.parametrize("name", LONG)
async def test_chunks_are_whole_entries_first_five(monkeypatch, name):
    echo(monkeypatch)
    text = (FX / name).read_text()
    entries = [raw for _, raw in split_entries(split_manuscript(text)[1])]
    got, doi = [], []
    ms = await parse_inputs(text, [], on_references=got.append, on_doi_refs=doi.append)
    assert sorted(len(c) for c in got) == sorted([5, 20, len(entries) - 25])
    by_size = sorted(got, key=len)
    assert any(len(c) == 5 and [r.raw for r in c] == entries[:5] for c in got), by_size
    for chunk in got:  # each chunk is a contiguous run of whole entries
        raws = [r.raw for r in chunk]
        start = entries.index(raws[0])
        assert raws == entries[start : start + len(raws)]
    assert [r.raw for r in ms.references] == entries
    # regex-only refs: exactly the entries carrying a DOI or arXiv id, copies, no LLM fields
    (pre,) = doi
    assert [r.raw for r in pre] == [r.raw for r in ms.references if r.doi or r.arxiv_id] and all(
        r.title is None for r in pre
    )
    assert {id(r) for r in pre}.isdisjoint(id(r) for r in ms.references)
    assert any(r.title for r in ms.references)


async def test_first_chunk_arrives_before_the_big_one(monkeypatch):
    echo(monkeypatch, delay=0.01)
    seen: list[tuple[float, int]] = []
    t0 = time.monotonic()
    await parse_inputs(
        (FX / "numbered_long.md").read_text(),
        [],
        on_references=lambda refs: seen.append((time.monotonic() - t0, len(refs))),
    )
    total = time.monotonic() - t0
    when = {size: t for t, size in seen}  # chunks of 5, 20 and the 2-entry tail
    assert when[5] < when[20] and when[5] < total / 2


async def test_doi_refs_come_before_any_llm_call(monkeypatch):
    order: list[str] = []
    model = EchoModel()
    orig = model._agenerate

    async def spy(*a, **kw):
        order.append("llm")
        return await orig(*a, **kw)

    object.__setattr__(model, "_agenerate", spy)
    monkeypatch.setattr(llm, "get_model", lambda role: model)
    await parse_inputs(
        (FX / "numbered_long.md").read_text(), [], on_doi_refs=lambda r: order.append("doi")
    )
    assert order[0] == "doi" and order.count("doi") == 1 and "llm" in order


async def test_raising_callbacks_do_not_break_parsing(monkeypatch):
    echo(monkeypatch)
    text = (FX / "numbered_long.md").read_text()
    plain = await parse_inputs(text, [])

    def boom(refs):
        raise RuntimeError("listener broke")

    ms = await parse_inputs(text, [], on_references=boom, on_doi_refs=boom)
    assert ms.model_dump() == plain.model_dump()
