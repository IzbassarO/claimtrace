# Tasks

Every task: branch `feat/<name>`, only the listed paths, unit tests offline, live tests in
`tests/live/` behind `RUN_LIVE=1`, PR to `main`. Contracts: `docs/ARCHITECTURE.md`.
Fixtures go under `tests/fixtures/<name>/`.

---

## T1 ingest — `feat/ingest`
Paths: `claimtrace/ingest/**`, `tests/test_ingest*.py`, `tests/fixtures/ingest/**`,
`tests/live/test_ingest_live.py`

- `parse_inputs(text, files)`: by extension — `.txt/.md` (manuscript text), `.bib` (references,
  `Reference.id` = bib key; bibtexparser 1.x), `.pdf` (GROBID `processFulltextDocument`, TEI →
  title, body, `biblStruct` references with `xml:id` as id, citations from `<ref type="bibr">`).
  Pasted text + `.bib` ⇒ body from text, references from bib. Unknown extension ⇒ warning.
- Plain-text manuscripts: split body / reference section at the last heading line
  `References|Bibliography|Literature Cited|Works Cited` (optionally numbered / `#`). Split entries
  by `[n]`, `n.`, `n)` prefixes, else one per line/blank-line block. Id = the number when present.
- Fields per reference string: DOI, arXiv id, year by regex (deterministic). Title / authors /
  venue via `core.llm.ask_json("extract", ...)` in batches, raw strings via `wrap_untrusted`.
  **Verbatim guard:** keep an extracted title only if it occurs in the raw string (whitespace- and
  case-insensitive), an author only if their family name occurs in it, a year only if the digits
  occur in it; otherwise drop the field and add a warning. LLM failure ⇒ keep regex fields, warn.
- Citations: sentence splitter that survives `et al.`, `e.g.`, `i.e.`, `Fig.`, decimals,
  initials. Markers: numeric `[1]`, `[1, 3]`, `[1-3]`, `[1–3]`; author-year `(Smith, 2020)`,
  `(Smith et al., 2020; Doe & Roe, 2019a)`, `Smith et al. (2020)`; LaTeX `\cite{a,b}` (stretch).
  Author-year resolves by first-author family name (case/diacritics-insensitive) + year (+suffix).
  Unresolved markers ⇒ `Citation` with empty/partial `ref_ids` + warning. Reference section is
  not part of `body` and produces no citations.
- `pdf_to_text(pdf)`: GROBID full text → body paragraphs as plain text. GROBID unreachable ⇒
  raise `RuntimeError("GROBID unavailable ...")` (pipeline handles it).
- GROBID URL: `get_settings().grobid_url`. Use `core.http.make_client()`.
- Tests: fixtures for numeric + author-year manuscripts, a .bib, a saved TEI XML (handwritten is
  fine), guard dropping a hallucinated title/year.

## T2 refaudit — `feat/refaudit`
Paths: `claimtrace/refaudit/**`, `tests/test_refaudit*.py`, `tests/fixtures/refaudit/**`,
`tests/live/test_refaudit_live.py`

- Providers (each a small async function → `list[Work]` or raises): Crossref (`/works/{doi}`;
  else `query.bibliographic`, rows 5; `mailto` from settings), OpenAlex (`/works/doi:{doi}`; else
  `search=` title, 5 results; rebuild abstract from `abstract_inverted_index`; `is_retracted`,
  `ids.pmid/pmcid`, `best_oa_location.pdf_url`), Semantic Scholar (`/graph/v1/paper/DOI:{doi}`
  else `/paper/search/match`; API key header if set), arXiv (`export.arxiv.org/api/query?id_list=`
  when an arXiv id is present). 404 on an id lookup is an *answer* (no record), not a failure.
  Per-provider concurrency limits; arXiv ≤ 1 request / 3 s. Use `core.http.request`.
- Deterministic matcher (`match.py`, pure functions, heavily unit-tested):
  title normalisation (casefold, strip accents/punctuation, collapse spaces), similarity
  (`difflib`), **negation rule** (differing sets of not/no/without/cannot/never/non/*n't ⇒ no
  match), **number rule** (differing digit tokens in titles ⇒ no match), year ±1, first-author
  family-name compare, DOI/arXiv normalisation (`https://doi.org/`, `doi:`, case, versions `v2`).
- Identity: DOI equal, or arXiv id equal, or (title match AND (year ±1 OR first author equal)).
  Pick the best identified record; merge abstract / pmcid / oa url / retraction from other
  identified records into `matched`.
- Status exactly as in ARCHITECTURE.md. Retraction: OpenAlex `is_retracted`, Crossref
  `updated-by`/`update-to` of type `retraction`, title starting `Retracted:`.
- Mismatch list with `cited` vs `found` strings. A DOI that resolves to a different title ⇒
  `metadata_mismatch` with a `title` mismatch (not `verified`).
- `ProviderOutcome` for every queried provider. Any failure + no match ⇒ `unchecked`.
- Tests: matcher table tests (negation, numbers, ±1, preprint vs journal), status logic with
  `MockTransport` (provider 503 ⇒ unchecked never not_found), parsers on saved JSON fixtures.

## T3 sources — `feat/sources`
Paths: `claimtrace/sources/**`, `tests/test_sources*.py`, `tests/fixtures/sources/**`,
`tests/live/test_sources_live.py`

- Order: Europe PMC full text (pmcid from `matched`, else search `DOI:"…"`; `fullTextXML` →
  JATS body text incl. figure/table captions, no reference list) → arXiv PDF
  (`https://arxiv.org/pdf/{id}` → injected `pdf_to_text`) → Unpaywall
  (`api.unpaywall.org/v2/{doi}?email=…` → `best_oa_location.url_for_pdf`, else `matched.oa_pdf_url`;
  only `application/pdf`, ≤ 30 MB) → abstract (`matched.abstract`; else OpenAlex / Semantic
  Scholar / Crossref abstract by DOI, JATS tags stripped).
- **Wrong-document guard:** reject a full text unless it is ≥ 2000 chars and most title words of
  `matched.title` appear in its first ~5000 chars; then fall through to the next source.
- Return `SourceText(kind, origin, url, text)`; `None` when nothing. Never synthesise text.
  Any exception inside one source ⇒ try the next.
- Tests with `MockTransport` and a fake `pdf_to_text`; JATS fixture.

## T4 claims — `feat/claims`
Paths: `claimtrace/claims/**`, `tests/test_claims*.py`, `tests/fixtures/claims/**`,
`tests/live/test_claims_live.py`

- `quote.py`: `normalize()` and `verify_quote(quote, source) -> bool` per AGENTS.md
  (≥ 5 words, word-boundary match, no NFKC, digits/indices untouched, `...` / ellipsis rejected).
  Exhaustive tests: `10 mg` vs `110 mg`, `CO2` vs `CO₂`, `x²` vs `x2`, `p<0.05` vs `p<0.01`,
  4-word quotes, partial-word starts/ends, curly vs straight quotes, line breaks.
- `retrieval.py`: passages (~3 sentences, overlapping); short texts (< 2500 chars) used whole;
  BM25 (rank-bm25) + fastembed (`BAAI/bge-small-en-v1.5`, lazy, in a thread) fused by reciprocal
  rank, top 6. Embedding failure ⇒ BM25 only. Unit tests must not download models (inject).
- Judge (`ask_json("judge", ..., ModelOpinion)`) then skeptic (`ask_json("skeptic", ...)`, sees the
  judge's opinion, hunts for overstatement: numbers, direction, population, causality, scope,
  hedging). Sentence and passages through `wrap_untrusted`.
- Combine: agree ⇒ that verdict; disagree ⇒ `needs_review` + more cautious verdict
  (supported < partly_supported < unsupported < insufficient_evidence). One model failed ⇒ the
  other's verdict + `needs_review`. Quote gate on the final verdict's quote (fallback: the other
  model's quote if it has the same verdict) against the **full** `source.text`; fail ⇒
  `insufficient_evidence` with a note. `source is None` ⇒ `source_not_found`, no LLM call.
- Fill `evidence_kind`, `source_url`, `judge`, `skeptic`.
- Tests with fake models: agreement, disagreement, invented quote, injection text in the source.

## T5 web — `feat/web`
Paths: `claimtrace/web/**`, `tests/test_web*.py`

- `claimtrace/web/app.py` exposes `app`. API per ARCHITECTURE.md. In-memory jobs (≤ 50 kept,
  oldest evicted), ≤ 2 running at once (others `queued`). Upload limits: extension whitelist,
  `MAX_UPLOAD_MB` total, at least one of text/files. Log `get_settings().problems()` at startup.
- Static UI in `claimtrace/web/static/` (plain HTML/CSS/JS, no build): paste + file upload,
  progress (stage, done/total, 1 s polling), summary counts, references table (status badge,
  mismatches cited→found, provider failures), claim cards (sentence, verdict, needs-review flag,
  verbatim quote, source link, "checked against abstract" / "checked against full text",
  rationales collapsible), export buttons, chat panel. **Only `textContent`, never `innerHTML`
  with data.** Keyboard-accessible, readable on mobile.
- `export.py`: report → Markdown. Chat (`chat.py`): `core.llm.ask_text("judge", ...)` with the
  report JSON via `wrap_untrusted`; answers only from the report, cites claim/ref ids.
- Tests: `fastapi.testclient` with `pipeline.run` monkeypatched; limits; export; chat with a fake.

## T6 eval — `feat/eval`
Paths: `eval/**`, `tests/test_eval*.py`

- `python -m eval.run --suite scifact|refs|all --limit N` → `eval/results/<suite>.json` and
  `eval/results/summary.md`. Calls module entry points directly (`claims.check_claim`,
  `refaudit.audit_references`) with `core.http.make_client()`.
- SciFact: download the release tarball into `eval/.cache/` (gitignored); dev claims with cited
  abstracts; seeded sample (default 100). Gold SUPPORT→supported, CONTRADICT→unsupported,
  no-evidence→not supported. Report: accuracy, precision/recall of `supported`,
  **false-supported rate** on CONTRADICT/NEI, quote-verified rate, needs-review rate, confusion.
- Injected-error references: `eval/data/refs_seed.jsonl` with ≥ 30 **real** references (built once
  with `eval/make_refs.py` from live Crossref lookups of well-known DOIs, committed), plus a few
  real retracted papers. Deterministic mutations ⇒ expected status: unchanged⇒verified,
  year+3⇒metadata_mismatch, negated title (with DOI)⇒metadata_mismatch, swapped first
  author⇒metadata_mismatch, fabricated paper⇒not_found, retracted⇒retracted. Report per-mutation
  accuracy, **false-verified count**, unchecked rate.
- Offline tests for mutations and metrics.

## T7 infra — `feat/infra`
Paths: `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `Caddyfile`, `.github/workflows/**`,
`docs/DEPLOY.md`, `scripts/**`

- Dockerfile: python 3.12 slim + uv, `uv sync --frozen --no-dev`, fastembed model pre-downloaded
  at build, non-root, `uvicorn claimtrace.web.app:app --host 0.0.0.0 --port 8000`.
- Compose: `app` (env_file `.env`, `GROBID_URL=http://grobid:8070`, healthcheck `/healthz`),
  `grobid` (official CRF image, pinned tag, healthcheck `/api/isalive`, works on amd64 Vultr; note
  arm64 caveats for local Mac), `caddy` (80/443, `Caddyfile` with `{$DOMAIN}` → `app:8000`,
  persistent volumes).
- CI: GitHub Actions — uv, `ruff check`, `ruff format --check`, `pytest -q`, `docker build`.
- `docs/DEPLOY.md`: Vultr Compute step by step (plan ≥ 4 GB RAM, Ubuntu, Docker, firewall
  22/80/443, DNS or `<ip-with-dashes>.sslip.io`, `.env` via scp, `docker compose up -d --build`,
  smoke test, logs, update procedure). `scripts/smoke.sh <base_url>` posting a tiny manuscript.
