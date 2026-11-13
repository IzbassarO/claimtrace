# AGENTS.md — rules for every agent and human in this repo

ClaimTrace audits citations in a manuscript: are the references real and correctly described,
and does each cited source actually say what the citing sentence claims?
Built from scratch during HackGlobal 2026. Code and docs in English.

## Hard rules

1. **Never read, copy, or import anything from `../claimtrace` or `../ct-*`.** That is an old
   prototype and is banned by hackathon rules. Note: your shell `PATH`/`VIRTUAL_ENV` may point at
   `../claimtrace/.venv` — ignore it. Always run tools through `uv run ...` (run
   `export VIRTUAL_ENV=` first to silence the warning). Never call bare `python`, `pytest`, `ruff`.
2. **Never read or print `.env`.** Use `uv run python -m claimtrace.core.settings` to check
   configuration (prints variable names only). New config goes into `core/settings.py` and
   `.env.example`, which only the lead edits.
3. **Stay in your paths.** Each task in `docs/TASKS.md` lists the paths you own. Touching anything
   else (especially `claimtrace/core/**`, `claimtrace/pipeline.py`, `pyproject.toml`, `uv.lock`)
   needs the lead. If you need a contract change, stop and say so in your PR description.
4. **Modules never import each other.** `ingest`, `refaudit`, `sources`, `claims` import only
   `claimtrace.core.*`, stdlib and third-party libs. Only `claimtrace/pipeline.py` (and the web
   shell / eval harness, which call the pipeline or module entry points) connect them. Functions
   one module needs from another are passed in as arguments (see `pdf_to_text`).
5. **Git:** one branch per task (`feat/<task>`), push it, open a PR to `main` with `gh pr create`.
   Never push to `main`, never merge, never force-push, never rewrite pushed history.
6. **Tests:** unit tests run with no network (use `httpx.MockTransport`, fake LLMs such as
   `langchain_core...FakeListChatModel`, or `ask_json(..., model=fake)`). Tests that hit real
   services live in `tests/live/` and run only with `RUN_LIVE=1`. Before pushing:
   `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` must pass.

## Correctness rules (these are the product)

- **A failure is never a negative finding.** Provider down / timeout / 429 / parse error ⇒
  `unchecked` (references) or `insufficient_evidence` (claims). Never `not_found`, never
  `unsupported`.
- **Existence and metadata are decided by deterministic code**, not by an LLM. LLMs may only
  extract fields from text; extracted fields must appear verbatim in the input or be dropped.
- **Title negation:** if the cited and found titles differ in negation words (`not`, `no`,
  `without`, `n't`, ...) the titles do **not** match, whatever the similarity score.
  Year tolerance is ±1.
- **No verdict without a verbatim quote.** `supported` / `partly_supported` / `unsupported` are
  shown only with a quote of ≥ 5 words found verbatim in the source text, matched at word
  boundaries. Normalisation may only collapse whitespace, drop soft hyphens / zero-width chars,
  straighten quotes and lowercase. **No NFKC/NFKD**, nothing that changes digits, signs,
  sub/superscripts or indices. Otherwise the verdict becomes `insufficient_evidence`.
- **Judge and skeptic run on different providers.** Disagreement ⇒ `needs_review = true` and the
  more cautious verdict (order: supported < partly_supported < unsupported <
  insufficient_evidence).
- **Untrusted text** (manuscript, source papers, uploaded files) goes into the LLM only through
  `core.llm.wrap_untrusted(...)`. LLM calls go only through `core.llm.ask_json` / `ask_text`:
  no `temperature`, no tool calling, JSON validated by pydantic with one retry.
- **The UI never renders untrusted text as HTML** (use `textContent`).

## Layout

See `docs/ARCHITECTURE.md` (contracts) and `docs/TASKS.md` (who owns what).
