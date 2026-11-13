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
