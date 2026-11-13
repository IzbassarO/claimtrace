"""Reference audit: Crossref/OpenAlex/Semantic Scholar/arXiv + deterministic matching.
Owner: T2 (docs/TASKS.md). Status semantics: docs/ARCHITECTURE.md."""

import asyncio
from collections.abc import Callable

import httpx

from claimtrace.core.schemas import ProviderOutcome, Reference, ReferenceAudit, RefStatus, Work
from claimtrace.core.settings import get_settings
from claimtrace.refaudit.match import (
    ARXIV_ID,
    arxiv_from_doi,
    id_equal,
    identifies,
    mismatches,
    norm_arxiv,
    norm_doi,
    strip_notice,
    title_match,
    title_similarity,
)
from claimtrace.refaudit.providers import (
    Gate,
    arxiv_batch,
    crossref,
    openalex_doi,
    openalex_search,
    semanticscholar,
)

PRIORITY = {"crossref": 0, "openalex": 1, "semanticscholar": 2, "arxiv": 3}
MERGE_FIELDS = ("doi", "arxiv_id", "pmid", "pmcid", "abstract", "oa_pdf_url", "venue")


def _same_work(a: Work, b: Work) -> bool:
    return bool(a.doi and a.doi == b.doi) or title_match(a.title, b.title)


def decide(ref: Reference, works: list[Work], outcomes: list[ProviderOutcome]) -> ReferenceAudit:
    """Pure status logic: provider answers -> ReferenceAudit."""
    out = ReferenceAudit(reference=ref, status=RefStatus.unchecked, providers=outcomes)
    identified = [w for w in works if identifies(ref, w)]
    if not identified:
        failed = [o.provider for o in outcomes if not o.ok]
        near = [w for w in works if title_match(ref.title, w.title)]
        notices = [w for w in works if title_match(ref.title, strip_notice(w.title))]
        if failed:
            out.note = f"no match; provider failed: {', '.join(failed)}"
        elif not outcomes:
            out.note = "no provider could be queried"
        elif notices and not near:
            w = notices[0]
            out.note = f"{w.provider} has only a correction/erratum notice for this title"
        elif near:
            w = near[0]
            out.note = (
                f"{w.provider} has a work with this title ({w.year}, "
                f"{w.authors[0] if w.authors else 'no authors'}) but neither year (±1) nor "
                "first author confirm it is the cited work"
            )
        else:
            out.status = RefStatus.not_found
            out.note = f"no matching record in {', '.join(o.provider for o in outcomes)}"
        return out

    # works found by the cited id, plus other versions of the same work (a journal record next
    # to the cited preprint); among those the one agreeing with the citation wins
    by_id = [w for w in identified if id_equal(ref, w)]
    anchored = [w for w in identified if any(w is v or _same_work(v, w) for v in by_id)]

    def rank(w: Work):
        sim = title_similarity(ref.title, w.title) if ref.title and w.title else 0.0
        return (
            not any(w is v for v in anchored),
            len(mismatches(ref, w)),
            -sim,
            PRIORITY[w.provider],
        )

    best = min(identified, key=rank)
    out.mismatches = mismatches(ref, best)
    matched = best.model_copy()
    for w in sorted(identified, key=rank):
        if w is not best and _same_work(best, w):
            for f in MERGE_FIELDS:
                if getattr(matched, f) is None and getattr(w, f) is not None:
                    setattr(matched, f, getattr(w, f))
    retracted_by = [w.provider for w in identified if w.is_retracted]
    matched.is_retracted = bool(retracted_by)
    out.matched = matched
    how = "identifier" if id_equal(ref, best) else "title + year/author"
    out.note = f"identified via {best.provider} ({how})"
    if retracted_by:
        out.status = RefStatus.retracted
        out.note += f"; retraction flagged by {', '.join(sorted(set(retracted_by)))}"
    elif out.mismatches:
        out.status = RefStatus.metadata_mismatch
    else:
        out.status = RefStatus.verified
    return out


def _outcome(provider: str, res) -> tuple[ProviderOutcome, list[Work]]:
    if isinstance(res, BaseException):
        err = f"{type(res).__name__}: {res}".rstrip(": ")
        return ProviderOutcome(provider=provider, ok=False, error=err), []
    return ProviderOutcome(provider=provider, ok=True, candidates=len(res)), res


def _ref_arxiv(ref: Reference) -> str | None:
    return norm_arxiv(ref.arxiv_id) or arxiv_from_doi(ref.doi)


async def _openalex(ref: Reference, client, gate, crossref_task) -> list[Work]:
    """Free DOI lookups first; the billed title search only if Crossref did not identify the
    work (or failed) and the cited DOI found nothing on OpenAlex."""
    doi = norm_doi(ref.doi)
    works = await openalex_doi(client, gate, doi) if doi else []
    if any(identifies(ref, w) for w in works):
        return works
    try:
        cr = await crossref_task
    except Exception:  # noqa: BLE001 - Crossref's failure is reported by its own outcome
        cr = []
    if hit := next((w.doi for w in cr if w.doi and identifies(ref, w)), None):
        return works + (await openalex_doi(client, gate, hit) if hit != doi else [])
    return works + (await openalex_search(client, gate, ref.title) if ref.title else [])


async def _audit_one(ref, client, gates, arxiv_task, use_s2) -> ReferenceAudit:
    aid = _ref_arxiv(ref)
    aid = aid if aid and ARXIV_ID.match(aid) else None
    if not (ref.title or norm_doi(ref.doi) or aid):
        return ReferenceAudit(
            reference=ref,
            status=RefStatus.unchecked,
            note="nothing to look up (no title, DOI or arXiv id)",
        )
    cr = asyncio.ensure_future(crossref(client, gates["crossref"], ref))
    calls = {"crossref": cr, "openalex": _openalex(ref, client, gates["openalex"], cr)}
    if use_s2:
        calls["semanticscholar"] = semanticscholar(client, gates["semanticscholar"], ref)
    results = await asyncio.gather(*calls.values(), return_exceptions=True)
    pairs = [_outcome(p, r) for p, r in zip(calls, results, strict=True)]
    if aid:
        try:
            res = (await arxiv_task)[aid]
        except Exception as e:  # noqa: BLE001
            res = e
        pairs.append(_outcome("arxiv", res))
    return decide(ref, [w for _, ws in pairs for w in ws], [o for o, _ in pairs])


async def audit_references(
    refs: list[Reference],
    client: httpx.AsyncClient,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ReferenceAudit]:
    # ponytail: limits are per call; two concurrent jobs double the rate to each provider
    gates = {
        "crossref": Gate(4),
        "openalex": Gate(4),
        "semanticscholar": Gate(1, 1.1),
        "arxiv": Gate(1, 3.0),
    }
    # keyless Semantic Scholar is mostly 429s, which would turn not_found into unchecked
    use_s2 = bool(get_settings().semantic_scholar_api_key.get_secret_value())
    ids = [a for r in refs if (a := _ref_arxiv(r))]
    arxiv_task = asyncio.ensure_future(arxiv_batch(client, gates["arxiv"], ids))
    done = 0

    async def one(ref: Reference) -> ReferenceAudit:
        nonlocal done
        try:
            res = await _audit_one(ref, client, gates, arxiv_task, use_s2)
        except Exception as e:  # noqa: BLE001 - one bad reference never sinks the batch
            res = ReferenceAudit(
                reference=ref, status=RefStatus.unchecked, note=f"audit failed: {type(e).__name__}"
            )
        done += 1
        if on_progress:
            on_progress(done, len(refs))
        return res

    try:
        return list(await asyncio.gather(*(one(r) for r in refs)))
    finally:
        arxiv_task.cancel()
