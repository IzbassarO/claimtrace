# ClaimTrace eval summary

## refs

2026-11-13T19:20:05+00:00 · params `{"limit": null, "seed": 0}`

| metric | value |
|---|---|
| n | 196 |
| accuracy | 0.964 |
| false_verified | 2 |
| false_not_found | 0 |
| unchecked_rate | 0.020 |

| mutation | n | accuracy | statuses |
|---|---|---|---|
| unchanged | 38 | 0.974 | metadata_mismatch 1, verified 37 |
| year_plus_3 | 38 | 1.000 | metadata_mismatch 38 |
| negated_title | 38 | 1.000 | metadata_mismatch 38 |
| swapped_first_author | 38 | 0.947 | metadata_mismatch 36, verified 2 |
| fabricated | 38 | 0.895 | not_found 34, unchecked 4 |
| retracted | 6 | 1.000 | retracted 6 |

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
