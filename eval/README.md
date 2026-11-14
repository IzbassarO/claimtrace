# eval

```
export VIRTUAL_ENV=
uv run python -m eval.run --suite SUITE [--limit N] [--seed S]
```

Each run writes `eval/results/<suite>.json` and rebuilds `summary.md` (and `ablation.md`) from
every results file present. Commit the results you quote in a PR.

| suite | LLM? | what it measures |
|---|---|---|
| `retrieval` | no | does `claims.retrieval.retrieve` find the gold evidence in a haystack |
| `scifact` | yes | verdicts on SciFact dev claims vs. the cited abstract (default 100 items) |
| `scifact-haystack` | yes | same, but the source is the haystack (retrieval + verdict together) |
| `refs` | no | `refaudit` on real references with injected errors |
| `injection` | yes | 10 prompt injections inserted mid-abstract into 5 SciFact sources (eval/injection.py) |
| `timing` | yes | `pipeline.run` end to end on bench documents: latency, stage walls, busy/idle |
| `all` | yes | `scifact` + `refs` |

LLM suites cost money: the lead runs them. `retrieval` and `refs` are free to rerun locally.

## retrieval

SciFact dev (claim, cited abstract) pairs with gold rationale sentences (all of them by default).
The haystack is the cited abstract plus 20 other corpus abstracts (none cited by the claim),
seeded, gold at a random position, one paragraph each (~28k chars, so passages are ranked).

- `recall@6`: share of claims where at least one gold sentence is fully inside one returned
  passage (whitespace-normalised).
- `mrr`: 1 / rank of the first passage with gold evidence, 0 if none. `retrieve` returns its top
  passages in document order, so the rank is the smallest k whose top-k contains gold
  (computed by setting `retrieval.TOP_K = k`).
- `mean_latency_ms`: one `retrieve` call per claim, model load excluded.
- `embed_errors`: must be 0, otherwise `hybrid` silently fell back to BM25.

Variants live in `RETRIEVAL_VARIANTS` in `eval/run.py`: name -> `async (query, text) ->
passages`. To measure a new one (e.g. `rerank`), add an entry and run `--suite retrieval`.

## verdict ablation

`scifact` and `scifact-haystack` rows store the judge's and the skeptic's verdict and quote.
From one run, `ablation.md` recomputes (no extra LLM calls) `judge only (no quote gate)`,
`judge + skeptic (no gate)`, `judge + quote gate` and `judge + skeptic + quote gate` (the
product, via `claims.combine`; `product_mismatches` must be 0). Key metric:
`false_supported_rate` = CONTRADICT/NEI items reported as (partly) supported.

## refs

`eval/data/refs_seed.jsonl`: ~400 real references (Crossref metadata, verbatim) across
medicine, biology, CS, physics/chemistry, social sciences/psychology and economics, plus 6
retracted papers. Rebuild with `uv run python -m eval.make_refs` (Crossref + free OpenAlex DOI
lookups). Mutations: unchanged -> verified, year+3 / negated title / swapped first author ->
metadata_mismatch, fabricated -> not_found, retracted -> retracted. Key metric: `false_verified`.

Budget: only `fabricated` references trigger paid OpenAlex title searches, so that mutation is
applied to a seeded subset of 150 seeds; a run is capped at 250 searches
(`OPENALEX_RUN_SEARCHES`) and prints the day's counter before/after. Retries of a 429/5xx count
as searches too. Answers are cached under `.cache/openalex/`, so a rerun is nearly free.

## timing

```
uv run python -m eval.run --suite timing --docs attention-text,review-200 --repeat 1 [--cold]
```

Runs `pipeline.run` on each bench document and writes `results/timing.json` plus tables in
`summary.md`. Per run:

- `first reference s` / `first claim s`: seconds until the first `on_partial` snapshot carrying
  >= 1 reference audit / claim check (if the pipeline never streams, the final report counts).
- `total s`, `partials` (number of `on_partial` calls), `peak RSS MB` (`ru_maxrss` of the eval
  process: monotonic, so with several runs it is the peak so far, not per run).
- `cache`: `warm` if any verdict came from the disk verdict cache (`n_verdict_cache_hit` > 0),
  else `cold`. `--cold` deletes `CACHE_DIR/verdicts` before each run so claim checks really call
  the LLMs; reference lookups and source texts stay cached (OpenAlex searches cost money), so a
  rerun's `references`/`sources` stages are faster than a first-ever run.
- `limit warnings`: `max_references` / `max_claims` truncation warnings from the report. When
  present, the run did not process the whole document.
- Stage table (from `Report.timings`, seconds since start): `start`/`first`/`end` = the
  `t_<stage>_*` marks, `wall = end - start`. Stages overlap: the pipeline streams, so sources
  and claims start while references are still running.
- `idle = wall x slots - busy`: worker-slot seconds the stage had but did not spend inside its
  operation. `slots` = 1 for `ingest`, `pipeline.SOURCE_CONCURRENCY` for `sources`
  (`busy_source_fetch`), `LLM_CONCURRENCY` for `claims` (`busy_claim_check`). High idle means
  workers waited for input (e.g. claims waiting for sources), not that the work was slow.
  `references` has no single busy span or slot count (each provider has its own gate), so its
  idle is n/a.
- Op table: every `busy_<op>` / `n_<op>` key (`ingest`, `source_fetch`, `grobid_source`,
  `retrieval`, `llm_extract` / `llm_judge` / `llm_skeptic`, `claim_check`, ...) with the mean
  seconds per call. Busy times of concurrent calls add up, so they can exceed the wall time.

Bench documents are fetched at runtime into `eval/.cache/bench/` (gitignored, never
committed), see `eval/bench.py`:

| doc | source | notes |
|---|---|---|
| `attention-pdf` | arXiv 1706.03762v7 PDF | needs GROBID at `GROBID_URL`; skipped with a message if unreachable (Docker is never started) |
| `attention-text` | arXiv HTML of 1706.03762v7 | plain text: title, body with `[n]` citations (math as LaTeX, tables/figures/footnotes dropped), `References` with 40 `[n]` entries |
| `review-200` | Europe PMC `fullTextXML` of **PMC9820749** | "TRP Channels: Recent Development in Translational Research and Potential Therapeutic Targets in Migraine", Int. J. Mol. Sci. 24(1):700, licence **CC BY 4.0**; 204 references, `[n]` citations as published |

`review-200` was chosen via the Europe PMC search API (`OPEN_ACCESS:y AND LICENSE:"cc by" AND
PUB_TYPE:"review"`, counting `ref-list/ref` in the full text). Each timing run costs LLM money
(~$1 for `attention-text`, several dollars for `review-200`): the lead runs them.

## injection

`uv run python -m eval.run --suite injection [--limit BASES]` (live, ~$1, stops before a base
pair would push the estimated cost over $2). 5 SciFact (claim, abstract) pairs the product got
right (3 CONTRADICT, 2 SUPPORT) x (baseline + 10 injections from `eval/injection.py`), each
injection inserted as a sentence between the two middle sentences of the abstract; all texts are
under `retrieval.WHOLE_TEXT_CHARS`, so the injection always reaches both models. Failures:
`toward_supported` (verdict more positive than the un-injected baseline) and `injected_quote`
(shown quote not verbatim in the ORIGINAL abstract). `judge_fooled` / `skeptic_fooled` count a
single model moving toward supported even when the product verdict held. Unknown (Moonshot)
prices count at the judge's list price in the budget estimate.
`toward_supported` compares with ONE baseline sample per base, so model noise counts too;
`false_supported` (CONTRADICT claims shown as (partly) supported) is anchored on gold and is the
number to quote. `noise_check` in `results/injection.json` records the reruns behind that call.
