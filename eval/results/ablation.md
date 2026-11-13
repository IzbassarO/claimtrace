# ClaimTrace verdict ablation

Recomputed from the judge's and skeptic's stored answers (no extra LLM calls).

## scifact-haystack-hybrid

2026-11-13T20:42:42+00:00 · params `{"limit": 60, "seed": 0}`

| variant | n | accuracy | false_supported_rate | supported_precision | supported_recall |
|---|---|---|---|---|---|
| judge only (no quote gate) | 60 | 0.817 | 0.222 | 0.724 | 0.875 |
| judge + skeptic (no gate) | 60 | 0.850 | 0.167 | 0.778 | 0.875 |
| judge + quote gate | 60 | 0.817 | 0.222 | 0.724 | 0.875 |
| judge + skeptic + quote gate | 60 | 0.850 | 0.167 | 0.778 | 0.875 |

product_mismatches (should be 0): 0

## scifact-haystack

2026-11-13T20:43:03+00:00 · params `{"limit": 60, "seed": 0}`

| variant | n | accuracy | false_supported_rate | supported_precision | supported_recall |
|---|---|---|---|---|---|
| judge only (no quote gate) | 60 | 0.850 | 0.194 | 0.759 | 0.917 |
| judge + skeptic (no gate) | 60 | 0.883 | 0.139 | 0.815 | 0.917 |
| judge + quote gate | 60 | 0.850 | 0.194 | 0.759 | 0.917 |
| judge + skeptic + quote gate | 60 | 0.883 | 0.139 | 0.815 | 0.917 |

product_mismatches (should be 0): 0

## scifact

2026-11-13T20:10:53+00:00 · params `{"limit": 100, "seed": 0}`

| variant | n | accuracy | false_supported_rate | supported_precision | supported_recall |
|---|---|---|---|---|---|
| judge only (no quote gate) | 100 | 0.870 | 0.167 | 0.787 | 0.925 |
| judge + skeptic (no gate) | 100 | 0.900 | 0.100 | 0.857 | 0.900 |
| judge + quote gate | 100 | 0.870 | 0.167 | 0.787 | 0.925 |
| judge + skeptic + quote gate | 100 | 0.900 | 0.100 | 0.857 | 0.900 |

product_mismatches (should be 0): 0
