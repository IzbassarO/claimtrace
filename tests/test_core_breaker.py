"""Circuit breaker in core.llm: a provider that keeps failing is skipped within one run."""

import asyncio

import httpx
import openai
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.core import llm, timing
from claimtrace.core import settings as st
from claimtrace.core.schemas import ModelOpinion

OK = '{"verdict": "supported", "quote": "a b c d e", "rationale": "r"}'


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(llm, "RATE_LIMIT_BACKOFF_S", 0.0)
    llm._limiters.clear()
    st.get_settings.cache_clear()
    yield
    llm._limiters.clear()


class Flaky(FakeListChatModel):
    calls: int = 0
    down: bool = True

    async def ainvoke(self, *a, **k):
        self.calls += 1
        if self.down:
            req = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
            raise openai.RateLimitError(
                "The engine is currently overloaded",
                response=httpx.Response(429, request=req),
                body=None,
            )
        return await FakeListChatModel.ainvoke(self, *a, **k)


async def ask(model):
    return await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=model)


async def test_breaker_skips_role_after_three_failures_then_probes_once(monkeypatch):
    monkeypatch.setattr(llm, "BREAKER_COOLDOWN_S", 0.2)
    t = timing.start()
    model = Flaky(responses=[OK] * 10)
    for _ in range(3):
        with pytest.raises(openai.RateLimitError):
            await ask(model)
    tripped = model.calls  # 3 failed calls x (1 + retries) attempts
    for _ in range(5):  # open: no provider call at all
        with pytest.raises(llm.LLMError, match="skipped"):
            await ask(model)
    assert model.calls == tripped and t.counts["llm_skeptic_skipped"] == 5
    await asyncio.sleep(0.25)
    model.down = False  # provider is back: the single probe succeeds and closes the breaker
    assert (await ask(model)).verdict == "supported"
    assert (await ask(model)).verdict == "supported"


async def test_failed_probe_reopens(monkeypatch):
    monkeypatch.setattr(llm, "BREAKER_COOLDOWN_S", 0.2)
    timing.start()
    model = Flaky(responses=[OK] * 10)
    for _ in range(3):
        with pytest.raises(openai.RateLimitError):
            await ask(model)
    await asyncio.sleep(0.25)
    before = model.calls
    with pytest.raises(openai.RateLimitError):  # the probe goes to the provider and fails
        await ask(model)
    assert model.calls > before
    with pytest.raises(llm.LLMError, match="skipped"):  # open again
        await ask(model)


async def test_no_breaker_outside_a_run():
    timing._current.set(None)
    model = Flaky(responses=[OK] * 10)
    for _ in range(5):
        with pytest.raises(openai.RateLimitError):
            await ask(model)
    assert model.calls == 5 * (llm.RATE_LIMIT_RETRIES + 1)
