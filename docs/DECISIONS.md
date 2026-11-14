# Decisions log

Newest last. Each entry: date/time (ET), decision, why. Negative results are results.

## Baseline before the autonomous tracks (Fri 14:41 ET, tag `stable-1` = `2a4cd5e`)

Models: judge `anthropic/claude-sonnet-5-5`, skeptic `moonshot/kimi-k3.1-pro-highspeed`, LLM_CONCURRENCY 8.

| Suite | n | Metric | Value |
|---|---|---|---|
| SciFact dev (cited abstracts, seed 0) | 100 | accuracy | 0.91 |
| | | false_supported_rate (CONTRADICT + NEI) | 0.10 |
| | | supported precision / recall | 0.86 / 0.925 |
| | | quote_verified_rate | 0.82 |
| | | quote_gate_violations | 0 |
| | | needs_review_rate | 0.20 |
| Refs (38 real + 6 retracted, 5 mutations) | 196 | accuracy | 0.954 |
| | | false_verified | 0 |
| | | false_not_found | 0 |
| | | unchecked_rate | 0.036 (all: Crossref failures under load) |
| Large PDF (Attention, 40 refs / 58 claims, Docker) | 1 | wall time / app peak RSS | 7 min / 0.71 GB |

Known at baseline: 11 strict-xfail tests (refaudit work-grouping bugs from the post-review
sweep, PR #44), fix in progress.

## Earlier decisions (Fri)

- **One model fails ⇒ the other's verdict + `needs_review`**; both fail ⇒ `insufficient_evidence`.
- **All sources down ⇒ `source_not_found`**: a statement about the source, never about the claim.
- **Venue is not compared** (provider venue strings are too inconsistent); the reference note lists
  the fields that were actually checked instead.
- **Number in an omitted subtitle may differ** ("…: a 10-year study"); negations are compared on
  full titles.
- **Spelling variants via explicit rewrites**, not edit distance (infection/injection,
  hypotension/hypertension must not match).
- **A field is a mismatch when no record of the same work agrees** (providers legitimately differ:
  preprint vs journal year, author order).
- **Skeptic model `kimi-k3.1-pro-highspeed`** (human's choice): 7 s vs 56 s per call for
  `kimi-k2.6`; SciFact accuracy 0.90 → 0.91.
- **Retrieval = BM25 shortlist (40) + dense rerank** instead of embedding every passage: 32 s →
  1.1 s per long source.
- **GROBID is reached only via `make_grobid_client()`** (no redirects); everything else must
  resolve to public IPs.

## Autonomous session (Fri from 14:41 ET)

- Live PDF tests are **skipped** while the human's CVAT stack is running (Docker VM memory);
  GROBID is not started.
- **Refaudit work grouping (PR #47)**: title + equal first author groups records without a year
  check (preprint 2014 vs journal 2017); authorless records on title + year ±1. Different first
  authors never group. Tightened in the next round after the tester found same-author/same-title
  different works (round 3).
- **UI "needs attention" (PR #48)**: claims need attention when `unsupported` or `needs_review`;
  `partly_supported` / `insufficient_evidence` / `source_not_found` are a separate open group
  ("partly supported or not checkable"). Counting them as attention gave "44 of 58" on the
  Attention paper — noise, not a signal.
- **Compose project name pinned to `claimtrace-hg` (PR #49)** so volumes are the same on the
  server and locally.
- **Incident (Fri ~15:05 ET)**: while verifying restarts (PR #49) GROBID was started via
  `depends_on` although the human's CVAT stack was running. Noticed by the lead, GROBID stopped
  within ~20 min; all 17 CVAT containers stayed up (no restarts, no exits). From now on every
  local `up` uses `--no-deps app caddy`.
- **A5 more full text (PR #51) — accepted**: abstract-only 26.5% → 20.4%, PDFs found 42.9% →
  49.0% on 49 fixed DOIs; +20 s over 49 DOIs (117 → 137 s). No effect on false verdicts
  (selection of sources only). NCBI `oa.fcgi` (404 / ftp only) and CORE (needs a key) skipped;
  bioRxiv often behind Cloudflare (429) — no bypass attempted. GROBID unavailable during the
  measurement, so PDF counts are an upper bound (guard not applied without text).
- **Incident (Fri ~16:00 ET, lead error)**: PR #57 set pytest `--basetemp=.cache/pytest-tmp`;
  `.cache/` does not exist in fresh checkouts, so CI and new worktrees errored on every test, and
  the lead's merge one-liner hid the failure (`| tail -1` swallowed the exit code) while merging
  #59. Fixed in #60 (`.pytest-tmp/` at the repo root). Since then every merge goes through a gate
  script with `pipefail`, an explicit pass/fail parse and a wait for green CI.
- **A4 number check — rejected (PR #61 closed)**: post-hoc on 100 SciFact rows nothing changes
  (5/100 claims contain a clear number; the only `supported` one quotes both numbers): accuracy
  0.91 → 0.91, false_supported_strict 1/60 → 1/60. The rule needs a numeric-heavy eval set to be
  justified; the branch is kept.
- **Eval harness (PR #58)**: retrieval suite = SciFact claims against a haystack (gold abstract +
  20 distractor abstracts, ≥ 23.5k chars), metric evidence recall@6 / MRR, no LLM.
  bm25 0.938 / 0.747 (3 ms), hybrid 0.976 / 0.798 (2.3 s CPU per claim). Refs set 399 seeds
  (393 real + 6 retracted, medicine/CS/physics/social science/economics/biology), 1728 cases:
  accuracy 0.956, false_verified 0, false_not_found 0; fabricated unchecked 67/150, 52 of them
  Crossref failures under load. 192 paid OpenAlex searches (cap 250).
- **Verdict ablation (SciFact n=100, seed 0)**: judge only 0.870 acc / 0.167 false-supported;
  judge+skeptic 0.900 / 0.100; the quote gate changes nothing on SciFact abstracts (short texts,
  all positive verdicts already quote verbatim) — its value is on full texts and adversarial
  quotes, covered by unit/adversarial tests, not by this metric.
- **A2 cross-encoder reranker — accepted (PR #68)**: retrieval (n=209) recall@6 0.976 → 0.990,
  MRR 0.798 → 0.852, +0.39 s per claim, peak RSS 0.5 GB. Verdicts on haystacks (n=60, same seed):
  accuracy 0.850 → 0.883, false-supported 0.167 → 0.139, precision 0.778 → 0.815, recall 0.875 →
  0.917. Model `Xenova/ms-marco-MiniLM-L-6-v2` pre-downloaded in the image.
- **A3 claim decomposition — not attempted**: 2–3× judge+skeptic calls per claim would break the
  "large PDF ≤ 7 min" constraint (currently 7 min) and eat the measurement budget; no cheap way to
  measure it on SciFact (claims are already atomic there).
- **Multi-DOI arXiv records (PR #67)**: `Work.doi` holds the space-joined list; comparisons use
  all of them for the preprint rule, the first otherwise. Display may show the joined string.
- **Merge gate hardened**: aborts on uncommitted worktree changes or if main isn't an ancestor
  after the merge (PR #68 was merged from a branch whose main-merge had silently failed; the
  combined main was re-verified: 519 passed, CI green).
- **A1 section-aware passages — rejected; section label — kept (PR #73)**: paragraph/section
  passages made retrieval worse on the haystack suite (n=209): product recall@6 0.990 → 0.971,
  MRR 0.852 → 0.834 (likely: one-sentence overlap across unrelated paragraphs). Passages were
  reverted to main's; the report now shows the source section of a verified quote ("§ 3 Results")
  only when exactly one section contains it, otherwise nothing (no label beats a wrong one).
- **DOI extraction hardening (PRs #67, #72)**: percent-decoding, invisible characters, multi-DOI
  arXiv fields, SICI `<>`, Markdown links, Unicode hyphens, smart quotes, URL query/fragment, HTML
  tags, hyphen line-wraps — each from a tester case where a real paper came out `not_found` or a
  DOI mismatch.

## Speed track (Fri evening)

- **Baseline breakdown** (40-ref Attention PDF, cold, Docker): 533 s total; ingest 17 s, references
  7 s, sources 24 s (GROBID 90 s busy over 28 PDFs), claims 485 s. Per claim 57 s: judge 2.9 s,
  skeptic 16.5 s, the rest waiting for the single global retrieval lock. Report appeared only at
  the end. Now recorded in every report (`Report.timings`) and in `eval --suite timing`.
- **Retrieval out of the global lock + per-source index cache (PR #82) — accepted**: offline
  58 claims / 20 sources 168 s → 66 s, identical passages, recall/MRR unchanged.
- **Streaming conveyor + partial reports + problem-first queue + verdict cache (PRs #85, #86,
  #87) — accepted**: no accuracy effect by construction; the existing pipeline tests pass
  unchanged and new tests check ordering/equality. Live: first result 533 s → 9 s, total 533 s →
  292 s (memory 1.19 GB).
- **Saved sample (PR #87) — accepted**: instant, labelled "Saved example", "Run live" button.
- **Retrieval prefetch outside LLM slots (PR #92, bounded in #93) — kept, effect unproven**: it
  does not change verdicts (same function, computed earlier); the one live run with it was
  confounded (providers 2–3× slower that hour: total 352 s) and peaked at 1.54 GB memory before
  the bound (#93). Not re-measured live (LLM budget).
- **Light PDF parser for sources (PR #88) — available, NOT default** (`SOURCE_PDF_PARSER=grobid`):
  offline coverage on 49 DOIs looked good (full text 77.6%), but the one live run had 7
  `source_not_found` claims vs 0 with GROBID; cannot show "not worse" ⇒ stays opt-in.
  arXiv HTML as a source (same PR) — accepted (XML-like, verbatim text).
- **Batched claims per source (PR #91) — rejected**: −56% LLM calls, −35% wall time, but accuracy
  never better in two runs (61 claims): 0.902/0.918 vs 0.885/0.885; false_supported 0.091/0.068 vs
  0.091/0.091 (one more false supported).
- **200-reference document**: references only (claims not run, LLM budget): first result 35 s
  (reference-field extraction by LLM dominates), 200 audits done at 45.5 s.
- **P0 Try a sample (PR #79)**: browsers send CRLF; reference heading not found ⇒ empty report.
  Fixed at the ingest entry; headings widened; CI checks the sample in the image.
- **Design skills (PR #81)**: installed project-locally, gitignored; `design-taste-frontend`'s
  auto-run hook removed; `web-design-guidelines` needs a network fetch ⇒ unused offline;
  `ui-ux-pro-max` scripts not run.

## Speed track closed (Fri night)

- **Per-provider concurrency (PR #97)**: `LLM_CONCURRENCY_SKEPTIC` with 429 back-off to
  `LLM_CONCURRENCY`; pipeline runs max(judge, skeptic) claim workers. Token counts per role in
  `Report.timings`; eval reports tokens + cost (PR #102; Anthropic list prices, Moonshot unknown).
- **Streaming reference list (PRs #99, #100)**: chunks of 5 then 20 entries are audited as soon as
  extracted; DOI/arXiv references warm the provider cache before extraction finishes (free
  lookups only); Crossref DOI records cached on disk. Not re-measured on the 200-ref document.
- **Verdict cache hardened (PR #96)**: only model opinions are cached; every hit re-runs
  `claims.finalize` (current quote gate, current source url).
- **Faster skeptic candidate** — judge is Anthropic, so an Anthropic skeptic (claude-haiku-5-5 as
  suggested) would break the different-providers rule; the candidate stayed on Moonshot:
  `kimi-k2.7-code-highspeed` (2.4 s vs 7.3 s per probe call). SciFact n=100 (seed 0): accuracy
  0.90 → 0.91, false-supported 0.10 → 0.083, precision 0.857 → 0.878, recall 0.90 → 0.90, quote
  verified 0.82 → 0.84 ⇒ **passes the selection rule**. Caveat: on the live Attention PDF it is
  stricter: `unsupported` 7 vs 2–4 before (2 harsher than warranted on method citations, 1 a
  genuine catch, 1 debatable arithmetic). SciFact counts NEI→unsupported as correct, so a false
  "unsupported" is not penalised there. Not switched in `.env` (human's file) — human's decision.
- **Final live run** (cold, candidate skeptic, 16 skeptic slots): first result 5.3 s, total 317 s,
  app peak 1.39 GB; LLM busy 604 s over 16 workers, retrieval 595 s busy over 2 slots ⇒ retrieval
  is now the bottleneck. **3-minute target not reached; speed track closed per the human's
  instruction.**
- **Faster skeptic candidate — REJECTED by the human (Fri 19:10 ET)**: `kimi-k2.7-code-highspeed`
  passed SciFact (0.91 / 0.083 vs 0.90 / 0.10) but gave no full-time gain on the live PDF (317 s
  vs 292 s; retrieval became the bottleneck) and added false-looking `unsupported` verdicts on
  method citations (7 vs 2–4). Skeptic stays `kimi-k3.1-pro-highspeed`. The human set
  `LLM_CONCURRENCY_SKEPTIC=16` in `.env`.
- **Retrieval threads/slots are settings now** (`RETRIEVAL_THREADS`, `RETRIEVAL_SLOTS`, defaults
  2 / 2) for the 4-vCPU server; the last speed pass tunes them in the container.
- **Retrieval in the container (PR #106) — accepted, last speed pass, no LLM**: the container
  was memory-bound (batch 16 of length-padded passages pushed the app to its 1.5 GB cap; 4
  threads OOM-killed), not CPU-quota bound. Length-sorted batches of 4: 50 real retrievals on
  cached sources 264 s → 161 s at 2×2 threads×slots, 103 s at 4×2; peak 1.44 GiB → 0.74 GiB;
  identical passages and recall@6/MRR. Settings: Mac 4/2, 4-vCPU server 2/2 (not measured on a
  real server). Projected effect on the 40-ref PDF (retrieval was ~300 s of the 317 s total):
  roughly 150–200 s, **not measured live** (no LLM budget for another run).
