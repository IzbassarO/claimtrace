"""Post-hoc simulation of the claims number cap over saved SciFact rows (no LLM calls).

uv run python -m eval.number_cap [eval/results/scifact.json]
"""

import json
import sys
from pathlib import Path

from claimtrace.claims.numbers import cap_numbers
from eval.run import scifact_metrics

KEYS = ["accuracy", "supported_precision", "supported_recall", "false_supported_rate"]


def strict(rows: list[dict]) -> dict:
    """Metrics on the literal `supported` verdict, which the cap can change."""
    sup = [r for r in rows if r["verdict"] == "supported"]
    neg = [r for r in rows if r["gold"] != "supported"]
    return {
        "supported_count": len(sup),
        "supported_precision_strict": sum(r["gold"] == "supported" for r in sup) / len(sup)
        if sup
        else None,
        "false_supported_strict": sum(r["gold"] != "supported" for r in sup) / len(neg)
        if neg
        else None,
    }


def simulate_number_cap(rows: list[dict]) -> dict:
    """-> {"before": {...}, "after": {...}, "changed": [rows whose verdict was capped]}."""
    after, changed = [], []
    for r in rows:
        verdict, note = cap_numbers(r["verdict"], r["quote"], r["claim"])
        if verdict != r["verdict"]:
            changed.append(r | {"verdict": verdict, "note": note})
        after.append(r | {"verdict": verdict})

    def metrics(rs):
        m = scifact_metrics(rs)
        return {k: m[k] for k in KEYS} | strict(rs)

    return {"before": metrics(rows), "after": metrics(after), "changed": changed}


if __name__ == "__main__":
    path = Path(
        sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "results/scifact.json"
    )
    print(json.dumps(simulate_number_cap(json.loads(path.read_text())["rows"]), indent=1))
