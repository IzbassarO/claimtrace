from eval.prices import run_cost


def test_run_cost_known_and_unknown_models(monkeypatch):
    counts = {"tokens_in_judge": 1_000_000, "tokens_out_judge": 100_000, "tokens_in_skeptic": 10}
    c = run_cost(counts, {"judge": "claude-sonnet-5-5", "skeptic": "kimi-x"})
    assert c["judge"]["usd"] == 3.0  # 1M * $2 + 0.1M * $10
    assert c["skeptic"]["usd"] is None and c["usd_total"] is None and c["usd_known_part"] == 3.0
    monkeypatch.setenv("EVAL_PRICE_KIMI_X", "1,2")
    assert run_cost(counts, {"judge": "claude-sonnet-5-5", "skeptic": "kimi-x"})["usd_total"] == 3.0
