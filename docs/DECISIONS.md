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
