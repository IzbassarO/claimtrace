# ClaimTrace eval summary

## refs

2026-11-13T18:42:33+00:00 · params `{"limit": 100, "seed": 0}`

| metric | value |
|---|---|
| n | 196 |
| accuracy | 0.974 |
| false_verified | 2 |
| false_not_found | 0 |
| unchecked_rate | 0.010 |

| mutation | n | accuracy | statuses |
|---|---|---|---|
| unchanged | 38 | 0.974 | metadata_mismatch 1, verified 37 |
| year_plus_3 | 38 | 1.000 | metadata_mismatch 38 |
| negated_title | 38 | 1.000 | metadata_mismatch 38 |
| swapped_first_author | 38 | 0.947 | metadata_mismatch 36, verified 2 |
| fabricated | 38 | 0.947 | not_found 36, unchecked 2 |
| retracted | 6 | 1.000 | retracted 6 |

## scifact

2026-11-13T18:42:06+00:00 · params `{"limit": 100, "seed": 0}`

| metric | value |
|---|---|
| n | 100 |
| accuracy | 0.900 |
| supported_precision | 0.857 |
| supported_recall | 0.900 |
| false_supported_rate | 0.100 |
| quote_verified_rate | 0.850 |
| quote_gate_violations | 0 |
| needs_review_rate | 0.240 |
| errors | 0 |

| gold \ verdict | counts |
|---|---|
| unsupported | unsupported 21 |
| nei | insufficient_evidence 15, partly_supported 5, supported 1, unsupported 18 |
| supported | partly_supported 24, supported 12, unsupported 4 |
