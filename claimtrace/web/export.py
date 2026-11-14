"""Report -> Markdown. Manuscript / source text is copied verbatim (whitespace flattened)."""

from claimtrace.core.schemas import ClaimCheck, ModelOpinion, ReferenceAudit, Report

EVIDENCE = {"full_text": "full text", "abstract": "abstract only"}
# as in the UI: a claim citing a missing or retracted reference is never shown as plainly supported
BAD_REF = {"not_found": "cited reference not found", "retracted": "cited reference retracted"}


def _single(c: ClaimCheck) -> str | None:
    """'judge only' / 'skeptic only' when exactly one model answered (as in the UI)."""
    if bool(c.judge) == bool(c.skeptic):
        return None
    return "judge only" if c.judge else "skeptic only"


def _disagree(c: ClaimCheck) -> bool:
    """The only case shown as "needs review": both models answered and differ."""
    return bool(c.judge and c.skeptic and c.judge.verdict != c.skeptic.verdict)


def _one_model_note(r: Report) -> str | None:
    n = sum(1 for c in r.claims if _single(c))
    m = sum(1 for c in r.claims if c.judge or c.skeptic)
    if not n:
        return None
    return (
        f"The second reader was unavailable for {n} of {m} claim{'' if m == 1 else 's'}; "
        "they were checked by one model. Run the audit again later for the two-reader check."
    )


def _one_line(s: str | None) -> str:
    return " ".join((s or "").split())


def _counts(d: dict[str, int]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(d.items())) or "none"


def _reference(a: ReferenceAudit) -> list[str]:
    out = [f"### [{a.reference.id}] {a.status.value}", "", f"- Cited: {_one_line(a.reference.raw)}"]
    if m := a.matched:
        found = _one_line(m.title) or "(untitled)"
        if m.year:
            found += f" ({m.year})"
        if m.doi:
            found += f" doi:{m.doi}"
        out.append(f"- Found ({m.provider}): {found}")
    out += [
        f"- Mismatch {x.field}: {_one_line(x.cited)} -> {_one_line(x.found)}" for x in a.mismatches
    ]
    failed = [p for p in a.providers if not p.ok]
    if failed:
        out.append("- Provider failures: " + ", ".join(f"{p.provider} ({p.error})" for p in failed))
    if a.note:
        out.append(f"- Note: {_one_line(a.note)}")
    return out + [""]


def _opinion(role: str, o: ModelOpinion | None) -> list[str]:
    return [f"- {role}: {o.verdict} — {_one_line(o.rationale)}"] if o else []


def _claim(c: ClaimCheck, ref_status: dict[str, str]) -> list[str]:
    head = f"### {c.citation_id} -> [{c.ref_id}] {c.verdict.value}"
    if bad := BAD_REF.get(ref_status.get(c.ref_id, "")):
        head += f" — {bad}"
    out = [
        head + (" (needs review)" if _disagree(c) else f" ({one})" if (one := _single(c)) else ""),
        "",
        f"> {_one_line(c.sentence)}",
        "",
    ]
    if c.quote:
        kind = EVIDENCE.get(c.evidence_kind.value, "") if c.evidence_kind else ""
        out.append(f'- Verbatim quote ({kind or "source"}): "{_one_line(c.quote)}"')
        if c.section:
            out.append(f"- Section: {_one_line(c.section)}")
    elif c.evidence_kind:
        out.append(f"- Checked against: {EVIDENCE[c.evidence_kind.value]}")
    if c.source_url:
        out.append(f"- Source: <{c.source_url}>")
    out += _opinion("Judge", c.judge) + _opinion("Skeptic", c.skeptic)
    if c.note:
        out.append(f"- Note: {_one_line(c.note)}")
    return out + [""]


def to_markdown(r: Report) -> str:
    out = [
        f"# ClaimTrace report: {_one_line(r.title) or 'untitled manuscript'}",
        "",
        f"Report `{r.id}`, created {r.created_at.isoformat(timespec='seconds')}.",
        "",
        *([f"> {note}", ""] if (note := _one_model_note(r)) else []),
        "## Summary",
        "",
        f"- References: {_counts(r.summary.get('references', {}))}",
        f"- Claims: {_counts(r.summary.get('claims', {}))}",
        "",
    ]
    if r.warnings:
        out += ["## Warnings", "", *[f"- {_one_line(w)}" for w in r.warnings], ""]
    out += ["## References", ""]
    for a in r.references:
        out += _reference(a)
    out += ["## Claims", ""]
    ref_status = {a.reference.id: a.status.value for a in r.references}
    for c in r.claims:
        out += _claim(c, ref_status)
    return "\n".join(out).rstrip() + "\n"
