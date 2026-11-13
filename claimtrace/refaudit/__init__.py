"""Reference audit: Crossref/OpenAlex/Semantic Scholar/arXiv + deterministic matching.
Owner: T2 (docs/TASKS.md). Status semantics: docs/ARCHITECTURE.md."""

import asyncio
from collections.abc import Callable
from difflib import SequenceMatcher

import httpx

from claimtrace.core.schemas import (
    Mismatch,
    ProviderOutcome,
    Reference,
    ReferenceAudit,
    RefStatus,
    Work,
)
from claimtrace.core.settings import get_settings
from claimtrace.refaudit.match import (
    ARXIV_ID,
    arxiv_from_doi,
    arxiv_of,
    compare,
    first_author_equal,
    id_equal,
    identifies,
    mismatches,
    norm_arxiv,
    norm_doi,
    normalize_title,
    strip_notice,
    title_match,
    title_prefix,
    title_similarity,
    year_ok,
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
    """Same DOI, same arXiv id, or same title confirmed by the first author (a preprint and its
    journal version may be years apart) or, when a record lists no authors, by the year (±1).
    A title alone never groups: 'Deep learning' is LeCun 2015 and Goodfellow 2016."""
    d, x = norm_doi(a.doi), arxiv_of(a)
    return (
        bool(d and d == norm_doi(b.doi))
        or bool(x and x == arxiv_of(b))
        or (
            title_match(a.title, b.title)
            and (
                first_author_equal(a.authors, b.authors)
                if a.authors and b.authors
                else year_ok(a.year, b.year)
            )
        )
    )


def _carries_doi(w: Work, doi: str) -> bool:
    a = arxiv_from_doi(doi)
    return norm_doi(w.doi) == doi or bool(a and arxiv_of(w) == a)


def _foreign_ids(ref: Reference, best: Work, works: list[Work]) -> list[Mismatch]:
    """A cited DOI / arXiv id that resolved to a record of a *different* work than `best`."""
    out: dict[str, Mismatch] = {}
    rd, ra = norm_doi(ref.doi), norm_arxiv(ref.arxiv_id) or arxiv_from_doi(ref.doi)
    for w in works:
        # untitled records prove nothing; versions of the same work are fine
        if w is best or not w.title or not best.title or _same_work(best, w):
            continue
        if (rd and rd == norm_doi(w.doi)) or (not ref.arxiv_id and ra and ra == arxiv_of(w)):
            field, cited = "doi", ref.doi
        elif ra and ra == arxiv_of(w):
            field, cited = "arxiv_id", ref.arxiv_id
        else:
            continue
        out[field] = Mismatch(field=field, cited=cited, found=f"resolves to: {w.title}")
    return list(out.values())


def _head_similarity(cited: str, found: str) -> float:
    """Similarity of the cited title to the found title's opening of the same length: a
    truncated citation with small edits ('A decomposable attention model' vs 'Decomposable
    attention models for ...')."""
    c, f = normalize_title(cited), normalize_title(found)
    return SequenceMatcher(None, c, f[: len(c)]).ratio() if len(c.split()) >= 3 else 0.0


def decide(ref: Reference, works: list[Work], outcomes: list[ProviderOutcome]) -> ReferenceAudit:
    """Pure status logic: provider answers -> ReferenceAudit."""
    out = ReferenceAudit(reference=ref, status=RefStatus.unchecked, providers=outcomes)
    identified = [w for w in works if identifies(ref, w)]
    if not identified:
        failed = [o.provider for o in outcomes if not o.ok]
        near = [w for w in works if title_match(ref.title, w.title)]
        # not_found must mean "nothing close exists": same first author, year (±1) when both
        # are known, and a prefix or very similar title is a possible match left for a human
        close = [
            w
            for w in works
            if ref.title
            and w.title
            and (
                (
                    first_author_equal(ref.authors, w.authors)
                    and (ref.year is None or w.year is None or year_ok(ref.year, w.year))
                    and (
                        title_prefix(ref.title, w.title)
                        or max(
                            title_similarity(ref.title, w.title),
                            _head_similarity(ref.title, w.title),
                        )
                        >= 0.85
                    )
                )
                # no cited authors to confirm with: a near-identical title + year is still close
                or (
                    not ref.authors
                    and year_ok(ref.year, w.year)
                    and (
                        title_prefix(ref.title, w.title)
                        or title_similarity(ref.title, w.title) >= 0.9
                    )
                )
            )
        ]
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
        elif close:
            w = close[0]
            out.note = f"possible match: {w.title} ({w.provider}, {w.year})"
        else:
            out.status = RefStatus.not_found
            out.note = f"no matching record in {', '.join(o.provider for o in outcomes)}"
        return out

    # works found by the cited id, plus other versions of the same work (a journal record next
    # to the cited preprint); among those the one agreeing with the citation wins
    by_id = [w for w in identified if id_equal(ref, w)]
    anchored = [w for w in identified if any(w is v or _same_work(v, w) for v in by_id)]

    # the cited id resolves to a record with the cited title: when that title is shared by
    # another paper, title + year + first author decide which one is cited, and the DOI of the
    # other ('Deep learning', LeCun vs Goodfellow) is the foreign one
    contested = any(title_match(ref.title, v.title) for v in anchored)

    def rank(w: Work):
        sim = title_similarity(ref.title, w.title) if ref.title and w.title else 0.0
        full = (
            contested
            and title_match(ref.title, w.title)
            and year_ok(ref.year, w.year)
            and first_author_equal(ref.authors, w.authors)
        )
        return (
            not (full or any(w is v for v in anchored)),
            len(mismatches(ref, w)),
            -sum(map(bool, (w.title, w.authors, w.year, w.doi))),  # coverage
            -sim,
            PRIORITY[w.provider],
        )

    best = min(identified, key=rank)
    if not any(id_equal(ref, w) or title_match(ref.title, w.title) for w in identified) and any(
        not _same_work(best, w) for w in identified
    ):
        titles = "; ".join(dict.fromkeys(f"'{w.title}'" for w in identified))
        out.note = f"truncated title fits several works: {titles}"
        return out
    # every record of the same work counts: a field one provider lacks is not checked there,
    # never an advantage over the provider that has it
    group = [w for w in identified if w is best or _same_work(best, w)]
    out.mismatches, checked = compare(ref, group)
    seen = {m.field for m in out.mismatches}
    out.mismatches += [m for m in _foreign_ids(ref, best, works) if m.field not in seen]
    doi_unconfirmed = None
    d1 = norm_doi(ref.doi)
    if d1 and "doi" not in {m.field for m in out.mismatches}:
        if not any(_carries_doi(w, d1) for w in works):
            # no record anywhere carries the cited DOI: its lookups answered 404, or failed
            look = {"crossref", "openalex"} | ({"arxiv"} if arxiv_from_doi(d1) else set())
            asked = [o for o in outcomes if o.provider in look]
            if failed := [o.provider for o in asked if not o.ok] or (not asked and ["none"]):
                doi_unconfirmed = f"cited DOI not confirmed; provider failed: {', '.join(failed)}"
            else:
                out.mismatches.append(
                    Mismatch(field="doi", cited=ref.doi, found="does not resolve")
                )
    matched = best.model_copy()
    for w in sorted(group, key=rank):
        for f in (*MERGE_FIELDS, "title", "authors", "year"):
            if not getattr(matched, f) and getattr(w, f):
                setattr(matched, f, getattr(w, f))
    retracted_by = [w.provider for w in group if w.is_retracted]
    matched.is_retracted = bool(retracted_by)
    out.matched = matched
    how = (
        "identifier"
        if id_equal(ref, best)
        else "title + year/author"
        if title_match(ref.title, best.title)
        else "truncated title + year + first author"
    )
    out.note = f"identified via {best.provider} ({how}); checked: {', '.join(checked) or 'ids'}"
    if retracted_by:
        out.status = RefStatus.retracted
        out.note += f"; retraction flagged by {', '.join(sorted(set(retracted_by)))}"
    elif out.mismatches:
        out.status = RefStatus.metadata_mismatch
    elif doi_unconfirmed:
        out.note += f"; {doi_unconfirmed}"
    elif not (
        any(c.startswith("title") for c in checked)
        and any(c.startswith(("authors", "year")) for c in checked)
    ):
        # verified needs the title and (first author or year) compared, not just an id resolved
        out.note = (
            f"identifier resolves to '{matched.title}'; "
            "cited title/authors could not be extracted to compare"
        )
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
    """Free DOI lookups first (cited DOI, then the arXiv DataCite DOI, then the DOI Crossref
    identified); the billed title search only if none of them identified the work."""
    aid = _ref_arxiv(ref)
    dois = [norm_doi(ref.doi), f"10.48550/arxiv.{aid}" if aid and ARXIV_ID.match(aid) else None]
    works, tried = [], []
    for doi in dict.fromkeys(d for d in dois if d):
        tried.append(doi)
        works += await openalex_doi(client, gate, doi)
        if any(identifies(ref, w) for w in works):
            return works
    try:
        cr = await crossref_task
    except Exception:  # noqa: BLE001 - Crossref's failure is reported by its own outcome
        cr = []
    if hit := next((w.doi for w in cr if w.doi and identifies(ref, w)), None):
        return works + (await openalex_doi(client, gate, hit) if hit not in tried else [])
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
