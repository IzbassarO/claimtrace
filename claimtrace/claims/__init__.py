"""Claim check: retrieval + judge + skeptic + verbatim quote gate. Owner: T4."""

from langchain_core.language_models.chat_models import BaseChatModel

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
    excerpts: list[str] | None = None,
) -> ClaimCheck:
    """Keyword args are test/eval hooks; `excerpts` lets the caller run retrieval ahead of time
    (the pipeline does it as soon as a source is ready, outside the LLM worker slots)."""
    base = {"citation_id": citation.id, "ref_id": ref_id, "sentence": citation.sentence}
    if source is None or not source.text.strip():
        return ClaimCheck(**base, verdict=Verdict.source_not_found)
    if excerpts is None:
        excerpts = await retrieve(citation.sentence, source.text, embed)
    user = _user_prompt(citation, source, excerpts)
    judge, judge_err = await _ask("judge", JUDGE_SYSTEM, user, judge_model)
    first = judge.model_dump_json() if judge else '{"unavailable": "first reviewer failed"}'
    skeptic_user = f"{user}\n\nFirst reviewer's answer:\n{wrap_untrusted('first_review', first)}"
    skeptic, skeptic_err = await _ask("skeptic", SKEPTIC_SYSTEM, skeptic_user, skeptic_model)
    return finalize(citation, ref_id, source, judge, skeptic, [judge_err, skeptic_err])


def finalize(
    citation: Citation,
    ref_id: str,
    source: SourceText,
    judge: ModelOpinion | None,
    skeptic: ModelOpinion | None,
    errors: list[str | None] | None = None,
) -> ClaimCheck:
    """Model opinions -> ClaimCheck: combine, verbatim quote gate against the full source text,
    section label, source fields. Pure; also used to re-derive cached opinions, so a cache hit
    always passes the CURRENT gate and carries the CURRENT source's url/kind."""
    verdict, quote, needs_review, note = combine(judge, skeptic, source.text)
    notes = [n for n in (*(errors or []), note) if n]
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
