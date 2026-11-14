"""Bench UI readout (app.js headline, counters, severity sort) shown more reassuring than the
report it renders. Runs the real app.js in node with the fake DOM of test_web_report_view.py;
skipped without node."""

import re

from test_web_report_view import find, ok, ref, render_calls, text

from claimtrace.core.schemas import (
    ClaimCheck,
    EvidenceKind,
    ProviderOutcome,
    ReferenceAudit,
    RefStatus,
    Report,
    Verdict,
)

down = [ProviderOutcome(provider="crossref", ok=False, error="ProviderError: HTTP 429")]


def claim(cid: str, rid: str, verdict: Verdict, **kw) -> ClaimCheck:
    return ClaimCheck(
        citation_id=cid, ref_id=rid, sentence=f"Sentence {cid} [{rid}].", **kw, verdict=verdict
    )


def test_headline_does_not_say_no_problems_when_nothing_could_be_checked():
    # every provider rate-limited and the LLM down: nothing was verified at all
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=ref(i, f"Title {i}"), status=RefStatus.unchecked, providers=down
            )
            for i in ("1", "2")
        ],
        claims=[claim("c1", "1", Verdict.insufficient_evidence, note="check failed: TimeoutError")],
    )
    out = render_calls([[report.model_dump(mode="json")]])
    # the summary line right below already says "2 of 2 references need attention"
    assert "2 of 2 references need attention" in text(out["summary"])
    assert "No problems found" not in text(out["headline"]), text(out["headline"])


def test_green_supported_counter_excludes_claims_citing_a_missing_reference():
    # the only claim cites a reference no provider knows (likely fabricated); its row is moved
    # to "Needs your attention" and never shown green, but the readout counter is
    report = Report(
        id="x",
        references=[
            ReferenceAudit(reference=ref("1", "Ghost"), status=RefStatus.not_found, providers=ok)
        ],
        claims=[
            claim(
                "c1",
                "1",
                Verdict.supported,
                quote="quantum folding of circadian proteins",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    out = render_calls([[report.model_dump(mode="json")]])
    claims_meter = out["meters"]["kids"][0]
    green = [
        b
        for b in find(claims_meter, lambda n: n["tag"] == "button")
        if find(b, lambda n: "s-ok" in n["cls"].split())
    ]
    counted = sum(int(text(b).split()[0]) for b in green)
    assert counted == 0, f"{counted} claim(s) under a green counter: {[text(b) for b in green]}"


def test_claim_citing_missing_reference_is_not_sorted_below_failed_checks():
    # judge/skeptic call failed (pipeline._failed): insufficient_evidence + needs_review, which
    # the readout key calls "never counted against you"; ref 9 does not exist at all
    refs = [
        ReferenceAudit(reference=ref("1", "Real paper"), status=RefStatus.verified, providers=ok),
        ReferenceAudit(reference=ref("9", "Ghost"), status=RefStatus.not_found, providers=ok),
    ]
    failed = [
        claim(
            f"c{i}",
            "1",
            Verdict.insufficient_evidence,
            needs_review=True,
            note="check failed: TimeoutError",
        )
        for i in (1, 2, 3)
    ]
    ghost = claim(
        "c4",
        "9",
        Verdict.supported,
        quote="ghost paper says exactly this thing",
        evidence_kind=EvidenceKind.abstract,
    )
    report = Report(id="x", references=refs, claims=[*failed, ghost])
    out = render_calls([[report.model_dump(mode="json")]])
    attention = text(out["claims"]).split("Needs your attention", 1)[1]
    order = re.findall(r"(c\d) → ref", attention)
    assert order[0] == "c4", order


def test_queue_position_shown_while_queued_and_absent_fields_fall_back():
    # #stage is not dumped by the harness: copy its text into #headline
    copy = '$("headline").textContent = $("stage").textContent;'
    out = render_calls(
        [f'renderStages({{status: "queued", queue_position: 2, queue_length: 3}}); {copy}']
    )
    assert text(out["headline"]) == "Waiting in queue — position 2 of 3"
    out = render_calls([f'renderStages({{status: "queued"}}); {copy}'])
    assert text(out["headline"]) == "Queued: waiting for a free worker"
