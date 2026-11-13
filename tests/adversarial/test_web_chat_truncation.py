"""Chat sees the report through report_json(). A report at the default reference limit must
still show the model its claim verdicts, or "which claims are unsupported?" gets a confident
"none"."""

from claimtrace.core.schemas import (
    ClaimCheck,
    ProviderOutcome,
    Reference,
    ReferenceAudit,
    RefStatus,
    Report,
    Verdict,
    Work,
)
from claimtrace.core.settings import get_settings
from claimtrace.web import chat


def ref_audit(i: int) -> ReferenceAudit:
    authors = [f"Author{i}{k}, Given" for k in range(6)]
    title = f"A randomized trial of intervention {i} on blood pressure in adults"
    return ReferenceAudit(
        reference=Reference(
            id=str(i),
            raw=f"{'; '.join(authors)}. {title}. J Hypertens. 2020;38(4):100-110.",
            title=title,
            authors=authors,
            year=2020,
            doi=f"10.1097/hjh.{i:07d}",
            venue="J Hypertens",
        ),
        status=RefStatus.verified,
        matched=Work(
            provider="crossref",
            provider_id=f"10.1097/hjh.{i:07d}",
            title=title,
            authors=authors,
            year=2020,
            doi=f"10.1097/hjh.{i:07d}",
            venue="Journal of Hypertension",
            url=f"https://doi.org/10.1097/hjh.{i:07d}",
        ),
        providers=[
            ProviderOutcome(provider=p, ok=True, candidates=1)
            for p in ("crossref", "openalex", "semanticscholar", "arxiv")
        ],
    )


def test_chat_report_keeps_claim_verdicts_at_reference_limit():
    n = get_settings().max_references  # 200 by default: a normal, allowed report size
    report = Report(
        id="big",
        references=[ref_audit(i) for i in range(1, n + 1)],
        claims=[
            ClaimCheck(
                citation_id="c7",
                ref_id="1",
                sentence="Intervention 1 cured hypertension [1].",
                verdict=Verdict.unsupported,
                quote="blood pressure did not differ between the two groups at 12 weeks",
            )
        ],
        summary={"references": {"verified": n}, "claims": {"unsupported": 1}},
    )
    text = chat.report_json(report)
    assert '"citation_id": "c7"' in text
    assert '"unsupported"' in text
