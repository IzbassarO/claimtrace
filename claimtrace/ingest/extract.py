"""Reference strings -> Reference: regex fields, then LLM title/authors/venue behind a verbatim
guard. The LLM never decides anything a regex can; whatever it returns must occur in the raw
string or is dropped with a warning."""

import asyncio
import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from claimtrace.core.llm import ask_json, wrap_untrusted
from claimtrace.core.schemas import Reference
from claimtrace.core.settings import get_settings
from claimtrace.ingest.text import family, find_arxiv, find_doi, year_candidates

BATCH = 20
SYSTEM = (
    "You split bibliographic reference strings into fields. For every item return its index, "
    "the title, the authors and the venue (journal, conference, book or publisher), each copied "
    "exactly as written in the string: do not correct, translate, expand or complete anything. "
    "Authors as 'Family, Given' using only the name parts written in the string; omit 'et al.'. "
    "Year: the publication year as written. Use null / [] for anything not present."
)


class _Item(BaseModel):
    index: int
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    year: int | None = None


class _Batch(BaseModel):
    items: list[_Item]


def _norm(s: str) -> str:
    """Whitespace- and case-insensitive; a line-break hyphenation 'meta-\\nanalysis' joins."""
    return re.sub(r"\s+", " ", re.sub(r"-\s+", "-", s)).strip().casefold()


def _in_raw(value: str, raw: str) -> bool:
    v = _norm(value)
    return bool(v) and v in _norm(raw)


def _title_in_raw(title: str, raw: str) -> bool:
    """Verbatim AND a whole field: the match must end where the title field ends (., ?, !,
    closing quote, '[Internet]'), so a truncated 'Aspirin does' of 'Aspirin does not ...' fails."""
    v = _norm(title)
    end = "" if v[-1:] in ("?", "!") else r"(?=$|[.?!]|,?[\"”’'»]|\s*\[)"
    return bool(v) and re.search(rf"(?<!\w){re.escape(v)}{end}", _norm(raw)) is not None


def _author_in_raw(author: str, raw: str) -> bool:
    """Family name and every written-out given name (not initials) occur as words in `raw`."""
    fam = family(author)
    if sum(c.isalpha() for c in fam) < 2:
        return False
    given = author.split(",", 1)[1] if "," in author else author.strip()[: -len(fam)]
    words = [fam] + [w for w in re.split(r"[\s.\-]+", given) if len(w) > 1]
    return all(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", raw, re.I) for w in words)


def regex_reference(ref_id: str, raw: str) -> Reference:
    years = year_candidates(raw)
    return Reference(
        id=ref_id,
        raw=raw,
        doi=find_doi(raw),
        arxiv_id=find_arxiv(raw),
        year=years[0] if years else None,
    )


def apply_guarded(ref: Reference, item: _Item, warnings: list[str]) -> None:
    """Copy LLM fields onto `ref` only if they occur verbatim in `ref.raw`."""
    raw, dropped = ref.raw, []
    title = (item.title or "").strip().rstrip(".").strip()
    if title:
        if _title_in_raw(title, raw):
            ref.title = title
        else:
            dropped.append("title")
    venue = (item.venue or "").strip().rstrip(".").strip()
    if venue:
        if _in_raw(venue, raw):
            ref.venue = venue
        else:
            dropped.append("venue")
    authors = [a.strip() for a in item.authors if a.strip() and "et al" not in a.lower()]
    ref.authors = [a for a in authors if _author_in_raw(a, raw)]
    if len(ref.authors) < len(authors):
        dropped.append(f"{len(authors) - len(ref.authors)} author(s)")
    if item.year is not None:
        cands = year_candidates(raw)
        if item.year in cands:
            ref.year = item.year  # LLM only picks among regex candidates
        elif str(item.year) not in raw:
            dropped.append("year")
    if dropped:
        warnings.append(
            f"Reference {ref.id}: extracted {', '.join(dropped)} not found verbatim; dropped"
        )


async def extract_references(
    entries: list[tuple[str, str]], model: BaseChatModel | None = None
) -> tuple[list[Reference], list[str]]:
    """[(id, raw)] -> References. LLM failure keeps the regex fields and adds a warning."""
    refs = [regex_reference(i, raw) for i, raw in entries]
    warnings: list[str] = []
    sem = asyncio.Semaphore(max(1, get_settings().llm_concurrency))

    async def run(batch: list[Reference]) -> None:
        lines = "\n".join(json.dumps({"index": k, "text": r.raw}) for k, r in enumerate(batch))
        user = "Reference strings, one JSON object per line:\n" + wrap_untrusted(
            "references", lines
        )
        try:
            async with sem:
                out = await ask_json("extract", SYSTEM, user, _Batch, model=model)
        except Exception as e:  # LLM down / bad JSON: keep regex fields, never guess
            warnings.append(
                f"Reference field extraction failed for {batch[0].id}..{batch[-1].id} "
                f"({type(e).__name__}); only DOI/arXiv/year used"
            )
            return
        for item in out.items:
            if 0 <= item.index < len(batch):
                apply_guarded(batch[item.index], item, warnings)

    await asyncio.gather(*(run(refs[i : i + BATCH]) for i in range(0, len(refs), BATCH)))
    return refs, warnings
