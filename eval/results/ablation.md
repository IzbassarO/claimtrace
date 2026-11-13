# ClaimTrace verdict ablation

Recomputed from the judge's and skeptic's stored answers (no extra LLM calls).

## scifact

2026-11-13T20:10:53+00:00 · params `{"limit": 100, "seed": 0}`

| variant | n | accuracy | false_supported_rate | supported_precision | supported_recall |
|---|---|---|---|---|---|
| judge only (no quote gate) | 100 | 0.870 | 0.167 | 0.787 | 0.925 |
| judge + skeptic (no gate) | 100 | 0.900 | 0.100 | 0.857 | 0.900 |
| judge + quote gate | 100 | 0.870 | 0.167 | 0.787 | 0.925 |
| judge + skeptic + quote gate | 100 | 0.900 | 0.100 | 0.857 | 0.900 |

product_mismatches (should be 0): 0
