import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # `eval` is a top-level folder, not an installed package

from eval.number_cap import simulate_number_cap  # noqa: E402

Q = "In the trial, a dose of {} lowered blood pressure in adults."


def row(gold, verdict, claim, quote):
    return {"gold": gold, "verdict": verdict, "claim": claim, "quote": quote,
            "quote_in_source": True, "needs_review": False, "error": None}  # fmt: skip


def test_simulate_number_cap():
    rows = [
        row("nei", "supported", "At 10 mg BP falls.", Q.format("110 mg")),  # capped
        row("supported", "supported", "At 10 mg BP falls.", Q.format("10 mg")),  # kept
        row("unsupported", "unsupported", "At 10 mg BP falls.", Q.format("110 mg")),  # untouched
    ]
    res = simulate_number_cap(rows)
    assert [r["verdict"] for r in res["changed"]] == ["partly_supported"]
    b, a = res["before"], res["after"]
    assert (b["supported_count"], a["supported_count"]) == (2, 1)
    assert (b["false_supported_strict"], a["false_supported_strict"]) == (0.5, 0.0)
    assert (b["supported_precision_strict"], a["supported_precision_strict"]) == (0.5, 1.0)
    # POSITIVE-based metrics cannot move: partly_supported is still POSITIVE
    for k in ("accuracy", "supported_precision", "supported_recall", "false_supported_rate"):
        assert a[k] == b[k]
