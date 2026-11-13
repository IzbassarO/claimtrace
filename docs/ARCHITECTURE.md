# Architecture

```
            text / .bib / .pdf
                    │
   web (FastAPI + static UI) ──► pipeline.run()  (LangGraph, linear)
                                   │
   ┌──────────┬───────────────┬────┴─────────┬──────────────┬─────────┐
   ▼          ▼               ▼              ▼              ▼
 ingest ──► refaudit ──────► sources ─────► claims ──────► report
 Manuscript  ReferenceAudit[] SourceText{}   ClaimCheck[]   Report
   │           │                 │              │
 GROBID     Crossref, OpenAlex  Europe PMC,    judge (provider A)
 extract LLM Semantic Scholar,  arXiv, Unpaywall skeptic (provider B)
 (verbatim   arXiv              (+GROBID via    BM25 + fastembed
  guard)     deterministic match  pdf_to_text)   verbatim quote gate
```

All shared types are in `claimtrace/core/schemas.py`. Modules exchange **only** those types.

## Packages

| Path | Role | May import |
|---|---|---|
| `claimtrace/core/` | schemas, settings, `llm` (role models, `ask_json`, `ask_text`, `wrap_untrusted`), `http` (`make_client` — adds `OPENALEX_API_KEY` to every api.openalex.org request, caches OpenAlex answers on disk, enforces the paid-search budget (402 when exhausted ⇒ provider failure), SSRF guard: every request and redirect hop must resolve to public IPs, no exceptions; `make_grobid_client()` reaches only the configured GROBID host and never follows redirects; retrying `request`; `redact`) | stdlib, 3rd party |
| `claimtrace/ingest/` | inputs → `Manuscript`; `pdf_to_text` via GROBID | core |
| `claimtrace/refaudit/` | `Reference` → `ReferenceAudit` | core |
| `claimtrace/sources/` | `ReferenceAudit` → `SourceText` | core (+ injected `pdf_to_text`) |
| `claimtrace/claims/` | (`Citation`, ref id, `SourceText`) → `ClaimCheck` | core |
| `claimtrace/pipeline.py` | LangGraph wiring, limits, progress, summary | everything above |
| `claimtrace/web/` | FastAPI app, jobs, export, chat, static UI | core, pipeline |
| `eval/` | `python -m eval.run` | core, module entry points |

## Module contracts (public entry points; signatures are fixed)

```python
# claimtrace/ingest/__init__.py
async def parse_inputs(text: str | None, files: list[InputFile]) -> Manuscript
async def pdf_to_text(pdf: bytes) -> str            # GROBID body text; raises on failure

# claimtrace/refaudit/__init__.py
async def audit_references(refs: list[Reference], client: httpx.AsyncClient,
                           on_progress: Callable[[int, int], None] | None = None
                           ) -> list[ReferenceAudit]   # same order as refs

# claimtrace/sources/__init__.py
async def fetch_source(audit: ReferenceAudit, client: httpx.AsyncClient,
                       pdf_to_text: Callable[[bytes], Awaitable[str]]) -> SourceText | None

# claimtrace/claims/__init__.py
async def check_claim(citation: Citation, ref_id: str, source: SourceText | None) -> ClaimCheck
```

Errors: entry points must not raise for per-item problems (one bad reference, one provider
down); they encode them in the result. The pipeline still guards each call and degrades to
`insufficient_evidence` / no source.

## Status semantics

**References** (`RefStatus`)
- `verified` — a provider record is identified (DOI/arXiv id equal, or title match + year ±1 or
  first-author match) and no field mismatches.
- `metadata_mismatch` — identified, but `mismatches` lists differing fields (title incl. negation,
  authors, year beyond ±1, doi, arxiv_id).
- `retracted` — identified and any provider flags retraction (wins over the two above;
  mismatches still listed).
- `not_found` — **every** queried provider answered successfully and none matched.
- `unchecked` — no match and at least one provider failed, or nothing to look up.

**Claims** (`Verdict`)
- `source_not_found` — no source text available (reference not identified, no text/abstract, or
  every source provider failed — a statement about the source, never about the claim).
- `supported` / `partly_supported` / `unsupported` — only with a verified verbatim quote.
- `insufficient_evidence` — models say so, quote fails verification, or both model calls failed.
  If only one model call failed, the other model's verdict is used with `needs_review = true`.
- `needs_review` — judge and skeptic disagree (or one of them failed).

## LLM roles

`extract` (reference-string parsing in ingest), `judge`, `skeptic` (claims). Provider + model per
role from `.env`; judge and skeptic providers must differ. Chat uses the `judge` role.
Anthropic via `langchain-anthropic`, Moonshot via `langchain-openai` with `MOONSHOT_BASE_URL`.

## Web API

- `POST /api/jobs` multipart: `text` (optional), `files` (0..n: .txt .md .bib .pdf) → `{"id"}`
- `GET /api/jobs/{id}` → `{"status": queued|running|done|error, "stage", "done", "total", "error", "report"}`
- `GET /api/jobs/{id}/export.json`, `GET /api/jobs/{id}/export.md`
- `POST /api/jobs/{id}/chat` `{"question", "history": [...]}` → `{"answer"}`
- `GET /healthz`

## Deployment

Docker Compose on one Vultr Compute instance: `app` (uvicorn), `grobid`, `caddy` (automatic
HTTPS for `$DOMAIN`). See `docs/DEPLOY.md`.
