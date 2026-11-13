"""Questions about one finished report, answered by the judge model from the report only."""

import json
from typing import Literal

from pydantic import BaseModel, Field

from claimtrace.core.llm import ask_text, wrap_untrusted
from claimtrace.core.schemas import Report

# ponytail: hard cut on the report size sent to the model; summarise per section if reports grow
MAX_REPORT_CHARS = 150_000

SYSTEM = (
    "You answer questions about one citation-audit report produced by ClaimTrace. "
    "Use only the report you are given; if it does not contain the answer, say so. "
    "Cite reference ids as [ref 3] and claim checks by citation id as [c12]. "
    "Never upgrade a finding: 'unchecked' means a lookup failed (not that a reference is fake), "
    "'insufficient_evidence' and 'source_not_found' mean the claim could not be checked. "
    "Quote source text only from the report's verified 'quote' fields. Answer briefly in "
    "plain text, without markdown or HTML."
)


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


def report_json(report: Report) -> str:
    data = report.model_dump(
        mode="json", exclude={"references": {"__all__": {"matched": {"abstract"}}}}
    )
    text = json.dumps(data, ensure_ascii=False)
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + " ... [report truncated]"
    return text


async def answer(report: Report, question: str, history: list[Turn]) -> str:
    parts = [wrap_untrusted("report_json", report_json(report))]
    if history:
        convo = "\n".join(f"{t.role}: {t.content}" for t in history)
        parts.append("Earlier conversation:\n" + wrap_untrusted("conversation", convo))
    parts.append(f"Question: {question}")
    return (await ask_text("judge", SYSTEM, "\n\n".join(parts))).strip()
