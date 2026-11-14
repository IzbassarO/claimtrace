"""Breaker (#130) is meant for pipeline runs only ("no run (unit tests, eval calling claims
directly): no breaker", core/llm.py). But `eval.run.main` calls `timing.start()` around every
suite, so the scifact / refs / injection evals get a breaker too: three provider hiccups in a
row and the skeptic is skipped for 60 s, so the eval silently scores judge-only verdicts as if
two readers had read them."""

import asyncio

import httpx
import openai
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import eval.run as er
from claimtrace.core import llm
from claimtrace.core.schemas import ModelOpinion


class Down(FakeListChatModel):
    calls: int = 0

    async def ainvoke(self, *a, **k):
        self.calls += 1
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://x"))


def test_eval_suite_calls_are_not_skipped_by_a_breaker(monkeypatch):
    monkeypatch.setattr(llm, "RATE_LIMIT_BACKOFF_S", 0.0)
    llm._limiters.clear()
    errors: list[str] = []

    async def fake_scifact(limit, seed):  # stands in for claims.check_claim calls in the suite
        model = Down(responses=["{}"])
        for _ in range(5):
            try:
                await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=model)
            except Exception as e:
                errors.append(type(e).__name__)
        return {"metrics": {}}

    monkeypatch.setattr(er, "run_scifact", fake_scifact)
    monkeypatch.setattr(er, "write_results", lambda *a, **k: None)
    asyncio.run(er.main(["--suite", "scifact"]))
    llm._limiters.clear()
    # every eval call must reach the provider; got 3 x APIConnectionError then 2 x LLMError
    assert errors == ["APIConnectionError"] * 5
