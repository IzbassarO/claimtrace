"""The only place modules meet. A streaming conveyor, not a sequence of stages:

ingest -> every reference audit starts; as soon as one finishes, its source is fetched (if it is
cited and identified); as soon as a source is ready, its claims are queued; claim workers
(LLM_CONCURRENCY) take problem references first. Partial reports are streamed via `on_partial`.

Progress callback: on_progress(stage, done, total). Timings: `Report.timings` (core/timing.py).
"""

import asyncio
import contextlib
import hashlib
import inspect
import itertools
import json
import logging
import os
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path

from claimtrace import claims, ingest, refaudit, sources
from claimtrace.core import timing
from claimtrace.core.http import make_client
from claimtrace.core.llm import concurrency, provider_of
from claimtrace.core.schemas import (
    Citation,
    ClaimCheck,
    InputFile,
    Manuscript,
    ModelOpinion,
    Reference,
    ReferenceAudit,
    RefStatus,
    Report,
    SourceText,
    Verdict,
)
from claimtrace.core.settings import get_settings

log = logging.getLogger(__name__)

SOURCE_TIMEOUT_S = 90  # whole fetch of one source (PDF download + GROBID), not per read
MAX_SENTENCE_CHARS = 2000  # longer "sentences" are tables/extraction noise; caps report size
SOURCE_CONCURRENCY = 6
# retrieval prefetches in flight; each builds/holds a source index, so this bounds memory
PREFETCH_CONCURRENCY = 2
MAX_CACHED_VERDICTS = 5000

Progress = Callable[[str, int, int], None]
Partial = Callable[[Report], None]


async def _timed_pdf_to_text(pdf: bytes) -> str:
    if get_settings().source_pdf_parser == "fast":
        with timing.span("fast_pdf_source"):
            return await ingest.pdf_to_text_fast(pdf)
    with timing.span("grobid_source"):
        return await ingest.pdf_to_text(pdf)


async def _excerpts(
    c: Citation, src: SourceText | None, sem: asyncio.Semaphore
) -> list[str] | None:
    """Retrieval ahead of the LLM queue; None (the claim retrieves itself) on any problem."""
    if src is None or not src.text.strip():
        return None
    try:
        async with sem:
            return await claims.retrieve(c.sentence, src.text)
    except Exception:
        log.exception("retrieval prefetch failed")
        return None


# ---------- verdict cache (claim + source text + models + prompts) ----------


def _verdict_dir() -> Path:
    return Path(get_settings().cache_dir) / "verdicts"


def _verdict_key(c: Citation, src: SourceText) -> str:
    s = get_settings()
    parts = [
        c.sentence,
        src.kind,
        hashlib.sha256(src.text.encode()).hexdigest(),
        f"{provider_of('judge')}/{s.llm_judge_model}",
        f"{provider_of('skeptic')}/{s.llm_skeptic_model}",
        hashlib.sha256((claims.JUDGE_SYSTEM + claims.SKEPTIC_SYSTEM).encode()).hexdigest(),
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def _cached_verdict(c: Citation, ref_id: str, src: SourceText) -> ClaimCheck | None:
    """Only the two model opinions are reused; gate, combine, section and source fields are
    re-derived now (claims.finalize), so a cache hit can't bypass a fixed quote gate or carry
    another source's url."""
    try:
        data = json.loads((_verdict_dir() / f"{_verdict_key(c, src)}.json").read_text())
        judge = ModelOpinion.model_validate(data["judge"])
        skeptic = ModelOpinion.model_validate(data["skeptic"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return claims.finalize(c, ref_id, src, judge, skeptic)


def _store_verdict(c: Citation, src: SourceText, res: ClaimCheck) -> None:
    # Only clean model answers are reusable; a failed call must be retried next time.
    if res.judge is None or res.skeptic is None:
        return
    d = _verdict_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{_verdict_key(c, src)}.json"
        tmp = f.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps({"judge": res.judge.model_dump(), "skeptic": res.skeptic.model_dump()})
        )
        tmp.replace(f)
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
        # ponytail: O(n) scan per write; fine at 5000 files, index it if the cap grows
        for old in files[: max(0, len(files) - MAX_CACHED_VERDICTS)]:
            old.unlink(missing_ok=True)
    except OSError as e:
        log.warning("verdict cache write failed: %s", e)


# ---------- helpers ----------


def _pairs(ms: Manuscript) -> list[tuple[Citation, str]]:
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
    return pairs


def _suspicious_first(refs: list[Reference]) -> list[int]:
    """Audit order: references with problem signals (no identifier, no title or no year) first,
    so likely problems show up early in the streamed report."""

    def looks_fine(r: Reference) -> bool:
        return bool(r.doi or r.arxiv_id) and bool(r.title and r.year)

    return sorted(range(len(refs)), key=lambda i: looks_fine(refs[i]))


def _failed(c: Citation, r: str, e: Exception) -> ClaimCheck:
    # LLM outage -> honest "insufficient_evidence", not a verdict
    return ClaimCheck(
        citation_id=c.id,
        ref_id=r,
        sentence=c.sentence,
        verdict=Verdict.insufficient_evidence,
        needs_review=True,
        note=f"check failed: {type(e).__name__}",
    )


def _report(rid, ms, audits, checks, complete: bool) -> Report:
    return Report(
        id=rid,
        title=ms.title,
        references=audits,
        claims=checks,
        warnings=list(ms.warnings),
        summary={
            "references": dict(Counter(a.status.value for a in audits)),
            "claims": dict(Counter(c.verdict.value for c in checks)),
        },
        complete=complete,
    )


async def _parse(text, files, on_references, on_doi_refs) -> Manuscript:
    params = inspect.signature(ingest.parse_inputs).parameters
    if "on_references" in params:
        return await ingest.parse_inputs(
            text, files, on_references=on_references, on_doi_refs=on_doi_refs
        )
    return await ingest.parse_inputs(text, files)


async def _audit(refs, client, on_progress, on_result, priority) -> list[ReferenceAudit]:
    params = inspect.signature(refaudit.audit_references).parameters
    if "on_result" in params:
        kw = {"on_result": on_result}
        if "priority" in params:
            kw["priority"] = priority
        return await refaudit.audit_references(refs, client, on_progress, **kw)
    # older refaudit without streaming: deliver results when all are done
    audits = await refaudit.audit_references(refs, client, on_progress)
    for i, a in enumerate(audits):
        on_result(i, a)
    return audits


# ---------- entry point ----------


async def run(
    text: str | None,
    files: list[InputFile] | None = None,
    on_progress: Progress | None = None,
    report_id: str | None = None,
    on_partial: Partial | None = None,
) -> Report:
    """`on_partial(report)` (optional) receives partial snapshots (`complete=False`) as reference
    audits and claim checks finish; the returned report is the final one (`complete=True`)."""
    user_progress = on_progress or (lambda *_: None)
    t = timing.start()
    rid = report_id or uuid.uuid4().hex[:12]

    def progress(stage: str, done: int, total: int) -> None:
        t.mark(f"{stage}_start")
        if done >= 1:
            t.mark(f"{stage}_first")
        if done >= total:
            t.mark(f"{stage}_end")
        user_progress(stage, done, total)

    limit = get_settings().max_references
    pairs: list[tuple[Citation, str]] = []
    pairs_by_ref: dict[str, list[tuple[int, Citation]]] = defaultdict(list)
    ms: Manuscript | None = None
    to_fetch = 0
    audits: dict[int, ReferenceAudit] = {}  # keyed by id() of the Reference object
    streamed: list[Reference] = []  # references in arrival order (before ingest finishes)
    checks: dict[int, ClaimCheck] = {}
    counters = {"sources": 0, "claims": 0}
    queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
    seq = itertools.count()
    fetches: list[asyncio.Task] = []
    source_sem = asyncio.Semaphore(SOURCE_CONCURRENCY)
    prefetch_sem = asyncio.Semaphore(PREFETCH_CONCURRENCY)

    def emit() -> None:
        if not on_partial:
            return
        order = ms.references if ms is not None else streamed
        snap = _report(
            rid,
            ms or Manuscript(body=""),
            [audits[id(r)] for r in order if id(r) in audits],
            [checks[k] for k in sorted(checks)],
            complete=False,
        )
        snap.timings = t.as_dict()
        try:
            on_partial(snap)
        except Exception:  # a UI callback must never break the audit
            log.exception("on_partial failed")

    async with make_client() as client:

        async def fetch_and_queue(audit: ReferenceAudit) -> None:
            src = None
            if audit.matched:
                async with source_sem:
                    try:
                        async with asyncio.timeout(SOURCE_TIMEOUT_S):
                            with timing.span("source_fetch"):
                                src = await sources.fetch_source(audit, client, _timed_pdf_to_text)
                    except Exception:  # incl. TimeoutError: degrade to "no source"
                        src = None
            counters["sources"] += 1
            progress("sources", counters["sources"], to_fetch)
            problem = audit.status != RefStatus.verified

            async def prepare(k: int, c: Citation) -> None:
                cached = src is not None and _cached_verdict(c, audit.reference.id, src) is not None
                ex = None if src is None or cached else await _excerpts(c, src, prefetch_sem)
                item = (0 if problem else 1, next(seq), k, c, audit.reference.id, src, ex)
                queue.put_nowait(item)

            await asyncio.gather(*(prepare(k, c) for k, c in pairs_by_ref[audit.reference.id]))

        def on_result(i: int, audit: ReferenceAudit) -> None:
            audits[id(audit.reference)] = audit
            progress("references", len(audits), len(ms.references) if ms else len(audits) + 1)
            emit()
            # claims are known only once ingest has finished; earlier audits are picked up then
            if ms is not None and audit.reference.id in pairs_by_ref:
                fetches.append(asyncio.ensure_future(fetch_and_queue(audit)))

        audit_tasks: list[asyncio.Task] = []

        def audit_chunk(chunk: list[Reference]) -> None:
            room = limit - len(streamed)
            chunk = chunk[: max(0, room)]
            if not chunk:
                return
            base = len(streamed)
            streamed.extend(chunk)
            audit_tasks.append(
                asyncio.ensure_future(
                    _audit(
                        chunk,
                        client,
                        lambda d, n: None,  # progress comes from on_result (chunks overlap)
                        lambda i, a: on_result(base + i, a),
                        _suspicious_first(chunk),
                    )
                )
            )

        async def warm(regex_refs: list[Reference]) -> None:
            # DOI/arXiv lookups before field extraction finishes: results are discarded, the
            # provider answers land in the disk cache (free lookups only, no paid search)
            with contextlib.suppress(Exception):
                await refaudit.audit_references(regex_refs[:limit], client)

        def on_doi_refs(regex_refs: list[Reference]) -> None:
            audit_tasks.append(asyncio.ensure_future(warm(regex_refs)))

        async def worker() -> None:
            while True:
                _, _, k, c, r, src, ex = await queue.get()
                try:
                    res = _cached_verdict(c, r, src) if src else None
                    if res is None:
                        try:
                            with timing.span("claim_check"):
                                res = await (
                                    claims.check_claim(c, r, src, excerpts=ex)
                                    if ex is not None
                                    else claims.check_claim(c, r, src)
                                )
                            if src:
                                _store_verdict(c, src, res)
                        except Exception as e:
                            res = _failed(c, r, e)
                    elif tc := timing.current():
                        tc.counts["verdict_cache_hit"] += 1
                    checks[k] = res
                    counters["claims"] += 1
                    progress("claims", counters["claims"], len(pairs))
                    emit()
                finally:
                    queue.task_done()

        workers = [
            asyncio.ensure_future(worker())
            for _ in range(max(1, concurrency("judge"), concurrency("skeptic")))
        ]
        try:
            progress("ingest", 0, 1)
            with timing.span("ingest"):
                ms = await _parse(text, files or [], audit_chunk, on_doi_refs)
            if len(ms.references) > limit:
                ms.warnings.append(
                    f"Only the first {limit} of {len(ms.references)} references audited."
                )
                ms.references = ms.references[:limit]
            progress("ingest", 1, 1)
            refs = ms.references
            # references ingest did not stream (older ingest / .bib / PDF): audit them now
            missing = [r for r in refs if all(r is not s for s in streamed)]
            if missing:
                audit_chunk(missing)
            pairs = _pairs(ms)
            for k, (c, r) in enumerate(pairs):
                pairs_by_ref[r].append((k, c))
            to_fetch = sum(1 for r in refs if r.id in pairs_by_ref)
            if pairs:
                progress("claims", 0, len(pairs))
            for a in list(audits.values()):  # audits that finished during ingest
                if a.reference.id in pairs_by_ref:
                    fetches.append(asyncio.ensure_future(fetch_and_queue(a)))
            await asyncio.gather(*audit_tasks)
            audit_list = [audits[id(r)] for r in refs]
            # a fetch may still be appending its claims; wait for every fetch, then the queue
            while any(not f.done() for f in fetches):
                await asyncio.gather(*fetches)
            await queue.join()
        finally:
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    report = _report(rid, ms, audit_list, [checks[k] for k in sorted(checks)], complete=True)
    report.timings = t.as_dict()
    progress("done", 1, 1)
    return report
