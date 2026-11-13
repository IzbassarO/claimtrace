"""Claim check: retrieval + judge + skeptic + verbatim quote gate. Owner: T4. STUB."""

from claimtrace.core.schemas import Citation, ClaimCheck, SourceText


async def check_claim(citation: Citation, ref_id: str, source: SourceText | None) -> ClaimCheck:
    raise NotImplementedError
