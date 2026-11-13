"""Report -> Markdown. Manuscript / source text is copied verbatim (whitespace flattened)."""

from claimtrace.core.schemas import ClaimCheck, ModelOpinion, ReferenceAudit, Report

EVIDENCE = {"full_text": "full text", "abstract": "abstract only"}


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


def _claim(c: ClaimCheck) -> list[str]:
    head = f"### {c.citation_id} -> [{c.ref_id}] {c.verdict.value}"
    out = [
        head + (" (needs review)" if c.needs_review else ""),
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
    for c in r.claims:
        out += _claim(c)
    return "\n".join(out).rstrip() + "\n"
