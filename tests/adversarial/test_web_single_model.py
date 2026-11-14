"""One model answered (the skeptic provider 429s): the backend sets needs_review, but the page,
the print view and the Markdown export must not call such a claim "Needs review". That label is
only for two readers who disagree; a single-model claim keeps its verdict plus "judge only"."""

import json
import re
import shutil
import subprocess

import pytest
from test_web_print import run as run_print
from test_web_report_view import HARNESS, JS, find, ok, ref, text

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

NOTICE = "The second reader was unavailable for {} of {}; they were checked by one model."


def render(r: Report) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    harness = HARNESS.replace('"saved", "saved-note", "report"]', '"one-model"]')
    p = subprocess.run(
        [node, "-e", harness, str(JS)],
        input=json.dumps([[r.model_dump(mode="json")]]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def claim(i: int, verdict: str, rid: str = "1", skeptic: str | None = None) -> ClaimCheck:
    quoted = verdict in ("supported", "partly_supported", "unsupported")
    return ClaimCheck(
        citation_id=f"c{i}",
        ref_id=rid,
        sentence=f"Sentence number {i} [{rid}].",
        verdict=Verdict(verdict),
        quote=f"a verbatim quote from the source {i}" if quoted else None,
        evidence_kind=EvidenceKind.full_text,
        judge=ModelOpinion(verdict=verdict, rationale="Judge says so."),
        skeptic=ModelOpinion(verdict=skeptic, rationale="Skeptic says so.") if skeptic else None,
        needs_review=True if skeptic is None else skeptic != verdict,
        note="skeptic unavailable — judge only" if skeptic is None else None,
    )


def judge_only_report() -> Report:
    refs = [
        ReferenceAudit(reference=ref("1", "Real paper"), status=RefStatus.verified, providers=ok),
        ReferenceAudit(reference=ref("2", "Ghost paper"), status=RefStatus.not_found, providers=ok),
    ]
    verdicts = ["unsupported", "partly_supported", "partly_supported", "supported", "supported"]
    verdicts += ["insufficient_evidence"]
    claims = [claim(i, v) for i, v in enumerate(verdicts, 1)]
    claims.append(claim(7, "supported", rid="2"))  # cites a reference that was not found
    return Report(id="x", references=refs, claims=claims)


def attention_ids(out: dict) -> list[str]:
    att = text(out["claims"]).split("Needs your attention", 1)[1]
    first_group = find(out["claims"], lambda n: n["cls"] == "fine")
    if first_group:
        att = att.split(text(first_group[0]), 1)[0]
    return sorted(set(re.findall(r"Sentence number (\d+)", att)))


def test_all_judge_only_report_is_not_needs_review_on_screen():
    out = render(judge_only_report())
    page = json.dumps(out).lower()
    assert "needs review" not in page and "need review" not in page
    # attention: the unsupported claim and the one citing a missing reference, not all 7
    assert attention_ids(out) == ["1", "7"]
    groups = [text(g) for g in find(out["claims"], lambda n: n["cls"] == "fine")]
    assert [g.split("·")[0] for g in groups] == ["Partly supported", "Supported", "Could not check"]
    assert "· 2" in groups[0] and "· 2" in groups[1] and "· 1" in groups[2]
    tags = {text(t) for t in find(out["claims"], lambda n: "tag" in n["cls"].split())}
    assert "judge only" in tags
    assert text(out["one-model"]).startswith(NOTICE.format(7, "7 claims"))
    assert out["one-model"]["hidden"] is False


def test_headline_is_never_clean_when_one_model_read_the_claims():
    r = judge_only_report()
    r.claims = [c for c in r.claims if c.verdict == Verdict.supported and c.ref_id == "1"]
    r.references = r.references[:1]
    head = text(render(r)["headline"])
    assert "No problems found" not in head
    assert head == "No claims contradicted · checked by one model for 2 of 2."
    r.claims.append(claim(9, "partly_supported"))
    assert text(render(r)["headline"]) == (
        "No claims contradicted · 1 partly supported · checked by one model for 3 of 3."
    )


def test_mixed_report_counts_n_of_m_and_keeps_needs_review_for_disagreement():
    r = judge_only_report()
    r.claims[1] = claim(2, "partly_supported", skeptic="supported")  # disagree -> needs review
    r.claims[3] = claim(4, "supported", skeptic="supported")  # agree -> plain
    # no model opinion at all (both failed): not counted in M, not "needs review"
    r.claims.append(
        ClaimCheck(
            citation_id="c8",
            ref_id="1",
            sentence="Sentence number 8 [1].",
            verdict=Verdict.insufficient_evidence,
            needs_review=True,
            note="both models failed",
        )
    )
    out = render(r)
    assert text(out["one-model"]).startswith(NOTICE.format(5, "7 claims"))
    assert attention_ids(out) == ["1", "2", "7"]
    tags = [text(t) for t in find(out["claims"], lambda n: "tag" in n["cls"].split())]
    assert tags.count("judge only") == 5
    marks = [text(m) for m in find(out["claims"], lambda n: "s-review" in n["cls"].split())]
    assert marks == ["Needs review"]
    review = [b for b in find(out["meters"], lambda n: n["tag"] == "button") if "review" in text(b)]
    assert [text(b) for b in review] == ["1 need review"]


def test_two_readers_report_has_no_notice():
    r = judge_only_report()
    r.claims = [
        claim(i, c.verdict.value, c.ref_id, skeptic=c.verdict.value) for i, c in enumerate(r.claims)
    ]
    out = render(r)
    assert out["one-model"]["hidden"] is True and text(out["one-model"]) == ""


def test_print_view_carries_the_notice_and_tags():
    out = run_print(judge_only_report())
    printed = text(out["print-report"])
    assert NOTICE.format(7, "7 claims") in printed
    assert "judge only" in printed and "needs review" not in printed.lower()
    assert "Needs your attention · 2" in printed


def test_markdown_carries_the_notice_and_judge_only_heading():
    r = judge_only_report()
    md = export.to_markdown(r)
    assert NOTICE.format(7, "7 claims") in md.split("## Summary")[0]
    assert "(needs review)" not in md
    assert "### c1 -> [1] unsupported (judge only)" in md
    r.claims[1] = claim(2, "partly_supported", skeptic="supported")
    md = export.to_markdown(r)
    assert "### c2 -> [1] needs review — judge: partly supported, skeptic: supported" in md
    assert NOTICE.format(6, "7 claims") in md
