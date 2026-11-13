"""Claim check: retrieval + judge + skeptic + verbatim quote gate. Owner: T4."""

import asyncio
import json

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel

from claimtrace.claims.quote import verify_quote
from claimtrace.claims.retrieval import Embed, retrieve, section_of
from claimtrace.core.llm import ask_json, wrap_untrusted
from claimtrace.core.schemas import Citation, ClaimCheck, ModelOpinion, SourceText, Verdict

# Most cautious last. Disagreement resolves to the later one.
CAUTION = ["supported", "partly_supported", "unsupported", "insufficient_evidence"]

_RULES = """Verdicts:
- supported: the source states what the citing sentence attributes to it.
- partly_supported: the source backs part of the claim, or a weaker/narrower version of it.
- unsupported: the source says something different or contradicts the claim.
- insufficient_evidence: the excerpts do not address the claim, or you cannot tell.
The citing sentence may cite several references; judge only what this one source could back.
"quote" must be copied character for character from the source excerpts: one contiguous span
of at least 5 words, no ellipsis, no edits, no added or dropped words. Use the quote that best
justifies your verdict. If the verdict is insufficient_evidence, the quote may be empty.
"rationale": one or two sentences."""

JUDGE_SYSTEM = (
    "You check citations in a scientific manuscript. Given a citing sentence and excerpts from "
    "the cited source, decide whether the source supports the sentence.\n" + _RULES
)

SKEPTIC_SYSTEM = (
    "You are a skeptical second reviewer of a citation check. A first reviewer judged whether a "
    "cited source supports a citing sentence. Look hard for overstatement: changed numbers or "
    "effect sizes, reversed direction, different population or setting, correlation presented "
    "as causation, broader scope than the source, dropped hedging or conditions. Give your own "
    "independent verdict; do not defer to the first reviewer.\n" + _RULES
)


def _user_prompt(citation: Citation, source: SourceText, excerpts: list[str]) -> str:
    kind = "abstract" if source.kind == "abstract" else "full text excerpts"
    return (
        f"Citing sentence:\n{wrap_untrusted('citing_sentence', citation.sentence)}\n\n"
        f"Cited source ({kind}):\n{wrap_untrusted('source_text', chr(10).join(excerpts))}"
    )


async def _ask(role, system, user, model) -> tuple[ModelOpinion | None, str | None]:
    try:
        return await ask_json(role, system, user, ModelOpinion, model=model), None
    except Exception as e:  # provider down / bad JSON: never a verdict
        return None, f"{role} failed: {type(e).__name__}"


def combine(
    judge: ModelOpinion | None, skeptic: ModelOpinion | None, source_text: str
) -> tuple[str, str | None, bool, str | None]:
    """-> (verdict, verified quote, needs_review, note). Pure, deterministic."""
    ops = [o for o in (judge, skeptic) if o]
    if not ops:
        return "insufficient_evidence", None, True, "both models failed"
    verdict = max((o.verdict for o in ops), key=CAUTION.index)
    needs_review = len(ops) < 2 or judge.verdict != skeptic.verdict
    if verdict == "insufficient_evidence":
        return verdict, None, needs_review, None
    for o in ops:
        if o.verdict == verdict and verify_quote(o.quote, source_text):
            return verdict, o.quote, needs_review, None
    note = f"models said {verdict}, but no quote was found verbatim in the source"
    return "insufficient_evidence", None, needs_review, note


async def check_claim(
    citation: Citation,
    ref_id: str,
    source: SourceText | None,
    *,
    judge_model: BaseChatModel | None = None,
    skeptic_model: BaseChatModel | None = None,
    embed: Embed | None = None,
) -> ClaimCheck:
    """Keyword args are test/eval hooks; the pipeline calls with the three positional ones."""
    if source is None or not source.text.strip():
        return ClaimCheck(
            citation_id=citation.id,
            ref_id=ref_id,
            sentence=citation.sentence,
            verdict=Verdict.source_not_found,
        )
    excerpts = await retrieve(citation.sentence, source.text, embed)
    user = _user_prompt(citation, source, excerpts)
    judge, judge_err = await _ask("judge", JUDGE_SYSTEM, user, judge_model)
    first = judge.model_dump_json() if judge else '{"unavailable": "first reviewer failed"}'
    skeptic_user = f"{user}\n\nFirst reviewer's answer:\n{wrap_untrusted('first_review', first)}"
    skeptic, skeptic_err = await _ask("skeptic", SKEPTIC_SYSTEM, skeptic_user, skeptic_model)
    return _result(citation, ref_id, source, judge, skeptic, [judge_err, skeptic_err])


def _result(
    citation: Citation,
    ref_id: str,
    source: SourceText,
    judge: ModelOpinion | None,
    skeptic: ModelOpinion | None,
    errors: list[str | None],
) -> ClaimCheck:
    verdict, quote, needs_review, note = combine(judge, skeptic, source.text)
    notes = [n for n in (*errors, note) if n]
    return ClaimCheck(
        citation_id=citation.id,
        ref_id=ref_id,
        sentence=citation.sentence,
        verdict=Verdict(verdict),
        quote=quote,
        section=section_of(quote, source.text) if quote else None,
        evidence_kind=source.kind,
        source_url=source.url,
        needs_review=needs_review,
        judge=judge,
        skeptic=skeptic,
        note="; ".join(notes) or None,
    )


# ---------- batched: several claims citing the same source, one call per role ----------

BATCH = 4  # max claims per batched call

_BATCH_TASK = (
    "You are given several numbered citing sentences (claims) that all cite the same source, "
    "with excerpts from that source. Judge each claim on its own, independently of the others, "
    "and only against the excerpts given for it. Return exactly one opinion per claim, with "
    '"index" set to the claim number.\n'
)
BATCH_JUDGE_SYSTEM = JUDGE_SYSTEM.replace("Given a citing sentence", "For each citing sentence")
BATCH_JUDGE_SYSTEM = BATCH_JUDGE_SYSTEM.replace(_RULES, _BATCH_TASK + _RULES)
BATCH_SKEPTIC_SYSTEM = SKEPTIC_SYSTEM.replace(_RULES, _BATCH_TASK + _RULES)


class IndexedOpinion(ModelOpinion):
    index: int


class Opinions(BaseModel):
    opinions: list[IndexedOpinion]


def _batch_prompt(cits: list[Citation], source: SourceText, excerpts: list[list[str]]) -> str:
    kind = "abstract" if source.kind == "abstract" else "full text excerpts"
    shared = all(e == excerpts[0] for e in excerpts)  # short source: sent whole, so only once

    def src(ex: list[str], whom: str) -> str:
        text = wrap_untrusted("source_text", "\n".join(ex))
        return f"Cited source ({kind}) for {whom}:\n{text}"

    parts = [src(excerpts[0], "every claim")] if shared else []
    for i, (c, ex) in enumerate(zip(cits, excerpts, strict=True), 1):
        part = f"Claim {i}. Citing sentence:\n{wrap_untrusted('citing_sentence', c.sentence)}"
        parts.append(part if shared else f"{part}\n{src(ex, f'claim {i}')}")
    return "\n\n".join(parts)


async def _ask_batch(role, system, user, n, model) -> dict[int, ModelOpinion]:
    """-> {0-based index: opinion}. An index that is missing, out of range or answered twice is
    left out (the caller re-checks it singly); a failed call returns {}."""
    try:
        got = await ask_json(role, system, user, Opinions, model=model)
    except Exception:  # provider down / bad JSON: the fallback decides, never a verdict
        return {}
    counts = {}
    for o in got.opinions:
        counts[o.index] = counts.get(o.index, 0) + 1
    return {
        o.index - 1: ModelOpinion(**o.model_dump(exclude={"index"}))
        for o in got.opinions
        if 1 <= o.index <= n and counts[o.index] == 1
    }


async def check_claims(
    items: list[tuple[Citation, str]],
    source: SourceText | None,
    *,
    judge_model: BaseChatModel | None = None,
    skeptic_model: BaseChatModel | None = None,
    embed: Embed | None = None,
) -> list[ClaimCheck]:
    """Claims citing the same source, (citation, ref id) each: one judge and one skeptic call per
    BATCH claims. Claims a batched answer leaves out are checked singly with `check_claim`.
    Quote gate, combine and notes are exactly as in `check_claim`. Results keep `items` order."""
    hooks = {"judge_model": judge_model, "skeptic_model": skeptic_model, "embed": embed}
    if len(items) > BATCH:
        chunks = [items[k : k + BATCH] for k in range(0, len(items), BATCH)]
        done = await asyncio.gather(*(check_claims(c, source, **hooks) for c in chunks))
        return [r for chunk in done for r in chunk]
    if len(items) < 2 or source is None or not source.text.strip():
        return [await check_claim(c, r, source, **hooks) for c, r in items]
    cits = [c for c, _ in items]
    excerpts = await asyncio.gather(*(retrieve(c.sentence, source.text, embed) for c in cits))
    user = _batch_prompt(cits, source, list(excerpts))
    judges = await _ask_batch("judge", BATCH_JUDGE_SYSTEM, user, len(items), judge_model)
    skeptics = {}
    if judges:
        first = json.dumps([{"index": i + 1, **judges[i].model_dump()} for i in sorted(judges)])
        skeptic_user = (
            f"{user}\n\nFirst reviewer's answers:\n{wrap_untrusted('first_review', first)}"
        )
        skeptics = await _ask_batch(
            "skeptic", BATCH_SKEPTIC_SYSTEM, skeptic_user, len(items), skeptic_model
        )
    results = {
        i: _result(cits[i], items[i][1], source, judges[i], skeptics[i], [])
        for i in judges
        if i in skeptics
    }
    missing = [i for i in range(len(items)) if i not in results]
    singles = await asyncio.gather(*(check_claim(*items[i], source, **hooks) for i in missing))
    results.update(zip(missing, singles, strict=True))
    return [results[i] for i in range(len(items))]
