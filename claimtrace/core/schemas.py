"""Shared data contracts. Modules exchange only these types (see docs/ARCHITECTURE.md).

Changing a field here is a cross-module change: do it in its own PR, owned by the lead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from claimtrace.core.http import redact


class RefStatus(StrEnum):
    verified = "verified"
    metadata_mismatch = "metadata_mismatch"
    not_found = "not_found"  # every queried provider answered and none matched
    retracted = "retracted"
    unchecked = "unchecked"  # no match and at least one provider failed: never not_found


class Verdict(StrEnum):
    supported = "supported"
    partly_supported = "partly_supported"
    unsupported = "unsupported"
    source_not_found = "source_not_found"
    insufficient_evidence = "insufficient_evidence"


class EvidenceKind(StrEnum):
    full_text = "full_text"
    abstract = "abstract"


# ---------- ingest ----------


class InputFile(BaseModel):
    name: str
    content: bytes


class Reference(BaseModel):
    id: str  # key that citations point at: "1", "12", "smith2020"
    raw: str  # original reference string / bib entry, verbatim
    title: str | None = None
    authors: list[str] = Field(default_factory=list)  # "Family, Given" or "Given Family"
    year: int | None = None
    doi: str | None = None  # bare, lower-case: "10.1000/xyz"
    arxiv_id: str | None = None  # "2101.00001" (no version) or old style "hep-th/9901001"
    venue: str | None = None


class Citation(BaseModel):
    id: str  # "c1", "c2", ... in document order
    sentence: str  # the citing sentence, verbatim from the manuscript
    ref_ids: list[str]  # Reference.id values it cites (resolved, may be empty)
    marker: str = ""  # the marker as written: "[3-5]", "(Smith et al., 2020)"


class Manuscript(BaseModel):
    title: str | None = None
    body: str = ""
    references: list[Reference] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ---------- reference audit ----------


class Work(BaseModel):
    """One bibliographic record as returned by a provider."""

    provider: Literal["crossref", "openalex", "semanticscholar", "arxiv"]
    provider_id: str | None = None
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    pmid: str | None = None
    pmcid: str | None = None
    venue: str | None = None
    url: str | None = None
    abstract: str | None = None
    is_retracted: bool = False
    oa_pdf_url: str | None = None


class Mismatch(BaseModel):
    field: Literal["title", "authors", "year", "doi", "venue", "arxiv_id"]
    cited: str
    found: str


class ProviderOutcome(BaseModel):
    provider: str
    ok: bool  # False = network error, timeout, 429/5xx after retries, unparsable answer
    error: str | None = None
    candidates: int = 0

    @field_validator("error")
    @classmethod
    def _redact(cls, v: str | None) -> str | None:
        return v and redact(v)


class ReferenceAudit(BaseModel):
    reference: Reference
    status: RefStatus
    matched: Work | None = None
    mismatches: list[Mismatch] = Field(default_factory=list)
    providers: list[ProviderOutcome] = Field(default_factory=list)
    note: str | None = None


# ---------- sources ----------


class SourceText(BaseModel):
    ref_id: str
    kind: EvidenceKind
    text: str
    origin: Literal[
        "europepmc", "arxiv", "biorxiv", "unpaywall", "openalex", "semanticscholar", "crossref"
    ]
    url: str | None = None

    @field_validator("url")
    @classmethod
    def _redact(cls, v: str | None) -> str | None:
        return v and redact(v)


# ---------- claims ----------


class ModelOpinion(BaseModel):
    """What one LLM said. Validated from its JSON answer."""

    verdict: Literal["supported", "partly_supported", "unsupported", "insufficient_evidence"]
    quote: str = ""  # must be copied verbatim from the source text
    rationale: str = ""


class ClaimCheck(BaseModel):
    citation_id: str
    ref_id: str
    sentence: str
    verdict: Verdict
    quote: str | None = None  # set only when verified verbatim in the source text
    evidence_kind: EvidenceKind | None = None
    source_url: str | None = None
    section: str | None = None  # heading of the source section the quote comes from, if known
    needs_review: bool = False
    judge: ModelOpinion | None = None
    skeptic: ModelOpinion | None = None
    note: str | None = None


# ---------- report ----------


class Report(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    title: str | None = None
    references: list[ReferenceAudit] = Field(default_factory=list)
    claims: list[ClaimCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    # {"references": {status: n}, "claims": {verdict: n}}
    summary: dict[str, dict[str, int]] = Field(default_factory=dict)
