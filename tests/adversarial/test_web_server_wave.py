"""Final server-wide wave: report counts that disagree between the screen and the printed copy.

Runs the real app.js in node with the fake DOM of test_web_print (no network, no browser).
"""

from test_web_print import run, text

from claimtrace.core.schemas import (
    ClaimCheck,
    EvidenceKind,
    ProviderOutcome,
    Reference,
    ReferenceAudit,
    RefStatus,
    Report,
    Verdict,
)
from claimtrace.web import export

ok = [ProviderOutcome(provider="crossref", ok=True, candidates=0)]


def test_print_does_not_count_a_claim_citing_a_missing_reference_as_supported():
    # Live sample (static/sample-report.json): 3 claims, the PDF says
    # "Claims: 1 not supported · 1 source not found · 1 supported · 1 cite a missing ..." = 4.
    # On screen the claim citing the missing reference is counted only as "cite a missing or
    # retracted reference" (claimKey), never under its own (here green) verdict.
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=Reference(id="1", raw="[1] Doe J. Invented paper. J. 2020.", title="X"),
                status=RefStatus.not_found,
                providers=ok,
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Invented results hold [1].",
                verdict=Verdict.supported,
                quote="invented results hold in every single case",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    printed = text(run(report)["print-report"])
    assert "1 cite a missing or retracted reference" in printed
    assert "1 supported" not in printed  # one claim, counted twice, once as green


def missing_ref_report() -> Report:
    return Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=Reference(id="1", raw="[1] Doe J. Invented paper. J. 2020.", title="X"),
                status=RefStatus.not_found,
                providers=ok,
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="Invented results hold [1].",
                verdict=Verdict.supported,
                quote="invented results hold in every single case",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
        summary={"references": {"not_found": 1}, "claims": {"supported": 1}},
    )


def test_markdown_claim_citing_a_missing_reference_says_so():
    # Download Markdown: the claim heading reads "c1 -> [1] supported" with no word that ref 1
    # does not exist; the screen tags it "reference not found" and never counts it as supported.
    md = export.to_markdown(missing_ref_report())
    claim = md.split("## Claims", 1)[1]
    assert "not found" in claim.lower() or "missing" in claim.lower()


def test_headline_does_not_say_no_problems_over_a_partly_supported_claim():
    # Every reference verified, the one claim "Partly supported" (the source backs only a
    # weaker / narrower version: an overstatement). Headline: "No problems found in 1 claim and
    # 1 reference." while the counter right below says "1 partly supported".
    report = Report(
        id="x",
        references=[
            ReferenceAudit(
                reference=Reference(id="1", raw="[1] Doe J. Real paper. J. 2020.", title="X"),
                status=RefStatus.verified,
                providers=ok,
            )
        ],
        claims=[
            ClaimCheck(
                citation_id="c1",
                ref_id="1",
                sentence="The drug cures all cancers [1].",
                verdict=Verdict.partly_supported,
                quote="the drug slowed tumour growth in some mice",
                evidence_kind=EvidenceKind.abstract,
            )
        ],
    )
    printed = text(run(report)["print-report"])
    assert "No problems found" not in printed


def test_mistyped_permalink_shows_a_page_not_raw_json():
    # Live: https://66-135-11-202.sslip.io/r/bad answers {"detail":"Unknown report."} as JSON;
    # a reader who mistypes / truncates a shared link sees a bare JSON blob, while a well-formed
    # unknown id gets the page with "Unknown report (it may have expired)."
    from fastapi.testclient import TestClient

    from claimtrace.web import app as web

    resp = TestClient(web.app).get("/r/bad")
    assert resp.headers["content-type"].startswith("text/html")
