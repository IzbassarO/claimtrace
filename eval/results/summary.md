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

2026-11-13T20:38:46+00:00 · params `{"limit": null, "seed": 0}`

| metric | value |
|---|---|
| n | 209 |
| haystack_chars_min | 23517 |
| embed_errors | 0 |

| variant | recall@6 | mrr | mean_latency_ms |
|---|---|---|---|
| bm25 | 0.938 | 0.747 | 3.044 |
| hybrid | 0.976 | 0.798 | 2266.518 |
| rerank | 0.990 | 0.852 | 2655.235 |

## scifact-haystack-hybrid

2026-11-13T20:42:42+00:00 · params `{"limit": 60, "seed": 0}`

| metric | value |
|---|---|
| n | 60 |
| accuracy | 0.850 |
| supported_precision | 0.778 |
| supported_recall | 0.875 |
| false_supported_rate | 0.167 |
| quote_verified_rate | 0.800 |
| quote_gate_violations | 0 |
| needs_review_rate | 0.250 |
| errors | 0 |

| gold \ verdict | counts |
|---|---|
| unsupported | partly_supported 1, unsupported 13 |
| nei | insufficient_evidence 10, partly_supported 5, unsupported 7 |
| supported | insufficient_evidence 2, partly_supported 9, supported 12, unsupported 1 |

## scifact-haystack

2026-11-13T20:43:03+00:00 · params `{"limit": 60, "seed": 0}`

| metric | value |
|---|---|
| n | 60 |
| accuracy | 0.883 |
| supported_precision | 0.815 |
| supported_recall | 0.917 |
| false_supported_rate | 0.139 |
| quote_verified_rate | 0.850 |
| quote_gate_violations | 0 |
| needs_review_rate | 0.217 |
| errors | 0 |

| gold \ verdict | counts |
|---|---|
| unsupported | partly_supported 1, unsupported 13 |
| nei | insufficient_evidence 9, partly_supported 4, unsupported 9 |
| supported | partly_supported 9, supported 13, unsupported 2 |

## scifact

2026-11-13T20:10:53+00:00 · params `{"limit": 100, "seed": 0}`

| metric | value |
|---|---|
| n | 100 |
| accuracy | 0.900 |
| supported_precision | 0.857 |
| supported_recall | 0.900 |
| false_supported_rate | 0.100 |
| quote_verified_rate | 0.820 |
| quote_gate_violations | 0 |
| needs_review_rate | 0.230 |
| errors | 0 |

| gold \ verdict | counts |
|---|---|
| unsupported | unsupported 21 |
| nei | insufficient_evidence 17, partly_supported 5, supported 1, unsupported 16 |
| supported | insufficient_evidence 1, partly_supported 18, supported 18, unsupported 3 |

## timing

2026-11-13T23:09:36+00:00 · params `{"docs": ["attention-pdf", "attention-text"], "repeat": 1, "cold": true}`

| metric | value |
|---|---|
| runs | 1 |
| skipped | 1 |

- skipped `attention-pdf`: GROBID not reachable at GROBID_URL (http://localhost:8070)

| doc | cache | first reference s | first claim s | total s | refs | claims | partials | peak RSS MB | limit warnings |
|---|---|---|---|---|---|---|---|---|---|
| attention-text#0 | cold | 25.070 | 25.070 | 141.040 | 40 | 58 | 98 | 173.200 | none |

### attention-text#0 stages (s since start; idle = wall x slots - busy)

| stage | start | first | end | wall | slots | busy | idle |
|---|---|---|---|---|---|---|---|
| ingest | 0.000 | 25.060 | 25.060 | 25.060 | 1 | 25.060 | 0.000 |
| references | 25.070 | 25.070 | 34.840 | 9.770 | n/a | n/a | n/a |
| sources | 25.070 | 25.070 | 50.530 | 25.460 | 6 | 130.480 | 22.280 |
| claims | 25.070 | 25.070 | 141.040 | 115.970 | 8 | 760.340 | 167.420 |

| op | n | busy s | mean s |
|---|---|---|---|
| claim_check | 58 | 760.340 | 13.110 |
| grobid_source | 33 | 99.530 | 3.020 |
| ingest | 1 | 25.060 | 25.060 |
| llm_extract | 3 | 32.950 | 10.980 |
| llm_judge | 56 | 135.700 | 2.420 |
| llm_skeptic | 56 | 623.480 | 11.130 |
| source_fetch | 34 | 130.480 | 3.840 |
