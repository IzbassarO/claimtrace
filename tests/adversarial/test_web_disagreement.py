"""Judge and skeptic disagree: the row reads "Needs review" with both verdicts, not a red
"Not supported" under the cautious verdict combine() keeps. "Not supported" (headline, counters,
filters, groups) is only for claims both readers agree on (or one model read)."""

import re

from test_web_print import run as run_print
from test_web_report_view import find, ok, ref, render_calls, text

from claimtrace.core.schemas import (
    ClaimCheck,
    EvidenceKind,
    ModelOpinion,
    ReferenceAudit,
    RefStatus,
    Report,
    Verdict,
)
from claimtrace.web import export

CAUTION = ["supported", "partly_supported", "unsupported", "insufficient_evidence"]


def claim(i: int, judge: str, skeptic: str, rid: str = "1") -> ClaimCheck:
    verdict = max(judge, skeptic, key=CAUTION.index)  # as combine() does
    return ClaimCheck(
        citation_id=f"c{i}",
        ref_id=rid,
        sentence=f"Sentence number {i} [{rid}].",
        verdict=Verdict(verdict),
        quote=f"a verbatim quote from the source {i}",
        evidence_kind=EvidenceKind.full_text,
        judge=ModelOpinion(verdict=judge, rationale="J."),
        skeptic=ModelOpinion(verdict=skeptic, rationale="S."),
        needs_review=judge != skeptic,
    )


def report(bad_ref: bool = True) -> Report:
    refs = [ReferenceAudit(reference=ref("1", "Real"), status=RefStatus.verified, providers=ok)]
    claims = [
        claim(1, "unsupported", "unsupported"),  # agreed: Not supported
        claim(2, "supported", "unsupported"),  # disagree: Needs review (cautious: unsupported)
        claim(3, "supported", "partly_supported"),  # disagree: Needs review (cautious: partly)
        claim(4, "supported", "supported"),
    ]
    if bad_ref:
        refs.append(
            ReferenceAudit(reference=ref("9", "Ghost"), status=RefStatus.not_found, providers=ok)
        )
        claims.append(claim(5, "supported", "supported", rid="9"))
    return Report(id="x", references=refs, claims=claims)


def render(r: Report, *js: str) -> dict:
    return render_calls([[r.model_dump(mode="json")], *js])


def rows(n) -> list:
    return find(n, lambda x: x["tag"] == "li" and "row" in x["cls"].split())


def label(row) -> str:
    return text(find(row, lambda n: n["cls"].startswith("mark"))[0])


def test_disagreement_row_reads_needs_review_with_both_verdicts():
    out = render(report())
    by_id = {re.search(r"Sentence number (\d)", text(r))[1]: r for r in rows(out["claims"])}
    assert label(by_id["2"]) == "Needs review" and label(by_id["3"]) == "Needs review"
    assert label(by_id["1"]) == "Not supported"
    assert "Judge: supported · Skeptic: not supported" in text(by_id["2"])
    assert "Judge: supported · Skeptic: partly supported" in text(by_id["3"])
    assert "Judge:" not in text(by_id["1"])
    # no red mark on a disagreement row
    assert not find(by_id["2"], lambda n: "s-bad" in n["cls"].split())


def test_attention_order_agreed_problems_first_then_needs_review():
    out = render(report())
    att = text(out["claims"]).split("Needs your attention", 1)[1]
    order = list(dict.fromkeys(re.findall(r"Sentence number (\d)", att)))  # expanded row: twice
    assert order[:4] == ["5", "1", "2", "3"]
    # the first row (desktop default) is the claim citing the missing reference, not a review row
    first = rows(out["claims"])[0]
    assert "Sentence number 5" in text(first) and "open" in first["cls"]


def test_counters_and_headline_count_disagreements_as_review():
    out = render(report())
    counts = [text(b) for b in find(out["meters"]["kids"][0], lambda n: n["tag"] == "button")]
    assert "1 not supported" in counts and "2 need review" in counts and "1 supported" in counts
    assert not any("partly" in c for c in counts)  # claim 3 is under "need review" only
    # two worst counts: the missing reference, then the agreed "not supported"
    assert text(out["headline"]) == "1 reference not found · 1 claim not supported."
    assert text(render(report(bad_ref=False))["headline"]) == (
        "1 claim not supported · 2 need review."
    )
    r = report(bad_ref=False)
    r.claims = r.claims[1:]
    assert text(render(r)["headline"]) == "2 claims need review."


def test_review_filter_shows_only_disagreements():
    out = render(report(), 'filter = "c:review"; renderReport(...lastRender);')
    shown = re.findall(r"Sentence number (\d)", text(out["claims"]))
    assert sorted(set(shown)) == ["2", "3"]
    out = render(report(), 'filter = "c:unsupported"; renderReport(...lastRender);')
    assert sorted(set(re.findall(r"Sentence number (\d)", text(out["claims"])))) == ["1"]


def test_print_view_shows_needs_review_with_both_verdicts():
    printed = text(run_print(report())["print-report"])
    assert "Needs reviewJudge: supported · Skeptic: not supported" in printed
    assert "Claims: 1 not supported · 1 supported · 1 cite a missing" in printed
    assert "2 need review" in printed


def test_markdown_heading_and_counts():
    md = export.to_markdown(report())
    assert "### c2 -> [1] needs review — judge: supported, skeptic: not supported\n" in md
    assert "### c1 -> [1] unsupported\n" in md
    assert "- Claims: needs review: 2, supported: 2, unsupported: 1" in md
