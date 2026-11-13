"""The only place modules meet. Linear LangGraph: ingest -> audit -> sources -> claims -> report.

Progress callback: on_progress(stage, done, total).
"""

import asyncio
import uuid
from collections import Counter
from collections.abc import Callable
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from claimtrace import claims, ingest, refaudit, sources
from claimtrace.core import timing
from claimtrace.core.http import make_client
from claimtrace.core.schemas import (
    ClaimCheck,
    InputFile,
    Manuscript,
    ReferenceAudit,
    Report,
    SourceText,
    Verdict,
)
from claimtrace.core.settings import get_settings

SOURCE_TIMEOUT_S = 90  # whole fetch of one source (PDF download + GROBID), not per read
MAX_SENTENCE_CHARS = 2000  # longer "sentences" are tables/extraction noise; caps report size

Progress = Callable[[str, int, int], None]


async def _timed_pdf_to_text(pdf: bytes) -> str:
    with timing.span("grobid_source"):
        return await ingest.pdf_to_text(pdf)


class State(TypedDict, total=False):
    text: str | None
    files: list[InputFile]
    manuscript: Manuscript
    audits: list[ReferenceAudit]
    sources: dict[str, SourceText]
    claims: list[ClaimCheck]
    report: Report


def _ctx(config: RunnableConfig):
    c = config["configurable"]
    return c["client"], c["progress"]


async def _ingest(state: State, config: RunnableConfig) -> State:
    _, progress = _ctx(config)
    progress("ingest", 0, 1)
    with timing.span("ingest"):
        ms = await ingest.parse_inputs(state.get("text"), state.get("files", []))
    limit = get_settings().max_references
    if len(ms.references) > limit:
        ms.warnings.append(f"Only the first {limit} of {len(ms.references)} references audited.")
        ms.references = ms.references[:limit]
    progress("ingest", 1, 1)
    return {"manuscript": ms}


async def _audit(state: State, config: RunnableConfig) -> State:
    client, progress = _ctx(config)
    refs = state["manuscript"].references
    progress("references", 0, len(refs))
    audits = await refaudit.audit_references(
        refs, client, lambda d, t: progress("references", d, t)
    )
    return {"audits": audits}


async def _sources(state: State, config: RunnableConfig) -> State:
    client, progress = _ctx(config)
    cited = {r for c in state["manuscript"].citations for r in c.ref_ids}
    todo = [a for a in state["audits"] if a.reference.id in cited and a.matched]
    done, out = 0, {}
    sem = asyncio.Semaphore(6)

    async def one(a: ReferenceAudit):
        nonlocal done
        async with sem:
            try:
                async with asyncio.timeout(SOURCE_TIMEOUT_S):
                    with timing.span("source_fetch"):
                        src = await sources.fetch_source(a, client, _timed_pdf_to_text)
            except Exception:  # incl. TimeoutError: degrade to "no source", never crash the job
                src = None
        if src:
            out[a.reference.id] = src
        done += 1
        progress("sources", done, len(todo))

    progress("sources", 0, len(todo))
    await asyncio.gather(*(one(a) for a in todo))
    return {"sources": out}


async def _claims(state: State, config: RunnableConfig) -> State:
    _, progress = _ctx(config)
    ms, srcs = state["manuscript"], state["sources"]
    known = {r.id for r in ms.references}
    long_ = [c for c in ms.citations if len(c.sentence) > MAX_SENTENCE_CHARS]
    if long_:
        ms.warnings.append(
            f"{len(long_)} citing sentence(s) longer than {MAX_SENTENCE_CHARS} characters skipped "
            "(not a sentence: likely a table or extraction artefact)."
        )
    pairs = [
        (c, r)
        for c in ms.citations
        if len(c.sentence) <= MAX_SENTENCE_CHARS
        for r in c.ref_ids
        if r in known
    ]
    limit = get_settings().max_claims
    if len(pairs) > limit:
        ms.warnings.append(f"Only the first {limit} of {len(pairs)} citation pairs checked.")
        pairs = pairs[:limit]
    sem = asyncio.Semaphore(get_settings().llm_concurrency)
    done = 0

    async def one(c, r) -> ClaimCheck:
        nonlocal done
        async with sem:
            try:
                with timing.span("claim_check"):
                    res = await claims.check_claim(c, r, srcs.get(r))
            except Exception as e:  # LLM outage -> honest "insufficient_evidence", not a verdict
                res = ClaimCheck(
                    citation_id=c.id,
                    ref_id=r,
                    sentence=c.sentence,
                    verdict=Verdict.insufficient_evidence,
                    needs_review=True,
                    note=f"check failed: {type(e).__name__}",
                )
        done += 1
        progress("claims", done, len(pairs))
        return res

    progress("claims", 0, len(pairs))
    return {"claims": list(await asyncio.gather(*(one(c, r) for c, r in pairs)))}


def _report(state: State, config: RunnableConfig) -> State:
    ms = state["manuscript"]
    report = Report(
        id=config["configurable"]["report_id"],
        title=ms.title,
        references=state["audits"],
        claims=state["claims"],
        warnings=ms.warnings,
        summary={
            "references": dict(Counter(a.status.value for a in state["audits"])),
            "claims": dict(Counter(c.verdict.value for c in state["claims"])),
        },
    )
    return {"report": report}


def build_graph():
    g = StateGraph(State)
    for name, fn in [
        ("ingest", _ingest),
        ("audit", _audit),
        ("sources", _sources),
        ("claims", _claims),
        ("report", _report),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "audit")
    g.add_edge("audit", "sources")
    g.add_edge("sources", "claims")
    g.add_edge("claims", "report")
    g.add_edge("report", END)
    return g.compile()


GRAPH = build_graph()


async def run(
    text: str | None,
    files: list[InputFile] | None = None,
    on_progress: Progress | None = None,
    report_id: str | None = None,
    on_partial: Callable[[Report], None] | None = None,
) -> Report:
    """`on_partial(report)` (optional) receives partial snapshots (`complete=False`) as reference
    audits and claim checks finish; the returned report is the final one (`complete=True`)."""
    user_progress = on_progress or (lambda *_: None)
    t = timing.start()

    def progress(stage: str, done: int, total: int) -> None:
        t.mark(f"{stage}_start")
        if done >= 1:
            t.mark(f"{stage}_first")
        if done >= total:
            t.mark(f"{stage}_end")
        user_progress(stage, done, total)

    async with make_client() as client:
        state = await GRAPH.ainvoke(
            {"text": text, "files": files or []},
            config={
                "configurable": {
                    "client": client,
                    "progress": progress,
                    "report_id": report_id or uuid.uuid4().hex[:12],
                }
            },
        )
    report = state["report"]
    report.timings = t.as_dict()
    progress("done", 1, 1)
    return report
