"""Ingest: manuscript text / .bib / PDF -> Manuscript. Owner: T1 (docs/TASKS.md). STUB."""

from claimtrace.core.schemas import InputFile, Manuscript


async def parse_inputs(text: str | None, files: list[InputFile]) -> Manuscript:
    raise NotImplementedError


async def pdf_to_text(pdf: bytes) -> str:
    """PDF -> plain body text via GROBID. Injected into sources by the pipeline."""
    raise NotImplementedError
