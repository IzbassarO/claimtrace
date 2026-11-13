# ClaimTrace eval summary

## refs

2026-11-13T19:53:38+00:00 · params `{"limit": null, "seed": 0}`

| metric | value |
|---|---|
| n | 1728 |
| accuracy | 0.956 |
| false_verified | 0 |
| false_not_found | 0 |
| unchecked_rate | 0.039 |
| openalex_searches | 192 |

| mutation | n | accuracy | statuses |
|---|---|---|---|
| unchanged | 393 | 0.977 | metadata_mismatch 9, verified 384 |
| year_plus_3 | 393 | 1.000 | metadata_mismatch 393 |
| negated_title | 393 | 1.000 | metadata_mismatch 393 |
| swapped_first_author | 393 | 1.000 | metadata_mismatch 393 |
| fabricated | 150 | 0.553 | not_found 83, unchecked 67 |
| retracted | 6 | 1.000 | retracted 6 |

## retrieval

2026-11-13T20:02:09+00:00 · params `{"limit": null, "seed": 0}`

| metric | value |
|---|---|
| n | 209 |
| haystack_chars_min | 23517 |
| embed_errors | 0 |

| variant | recall@6 | mrr | mean_latency_ms |
|---|---|---|---|
| bm25 | 0.938 | 0.747 | 3.177 |
| hybrid | 0.976 | 0.798 | 2303.662 |

## scifact

2026-11-13T19:19:27+00:00 · params `{"limit": 100, "seed": 0}`

| metric | value |
|---|---|
| n | 100 |
| accuracy | 0.910 |
| supported_precision | 0.860 |
| supported_recall | 0.925 |
| false_supported_rate | 0.100 |
| quote_verified_rate | 0.820 |
| quote_gate_violations | 0 |
| needs_review_rate | 0.200 |
| errors | 0 |

| gold \ verdict | counts |
|---|---|
| unsupported | unsupported 21 |
| nei | insufficient_evidence 17, partly_supported 5, supported 1, unsupported 16 |
| supported | insufficient_evidence 1, partly_supported 18, supported 19, unsupported 2 |
