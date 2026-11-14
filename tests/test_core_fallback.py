"""Skeptic fallback model (core.llm.ask_json): used only in pipeline runs, only when the primary
skeptic is skipped by its breaker or finally fails with a transient error."""

import json

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
    st.get_settings.cache_clear()


class Named(FakeListChatModel):
    model_name: str = "kimi-k2.7-code-highspeed"
    calls: int = 0

    async def ainvoke(self, *a, **k):
        self.calls += 1
        return await FakeListChatModel.ainvoke(self, *a, **k)


class Overloaded(Named):
    model_name: str = "kimi-k3.1-pro-highspeed"

    async def ainvoke(self, *a, **k):
        self.calls += 1
        req = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
        raise openai.RateLimitError(
            "The engine is currently overloaded",
            response=httpx.Response(429, request=req),
            body=None,
        )


class Broken(Named):
    async def ainvoke(self, *a, **k):
        self.calls += 1
        raise ValueError("not transient")


async def ask(primary, fallback):
    return await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=primary, fallback=fallback)


async def test_transient_failure_uses_fallback_and_records_its_model():
    t = timing.start(breakers=True)
    out = await ask(Overloaded(responses=[OK]), Named(responses=[OK]))
    assert out.verdict == "supported" and out.model == "kimi-k2.7-code-highspeed"
    assert t.counts["skeptic_fallback_used"] == 1


async def test_open_breaker_goes_straight_to_fallback():
    timing.start(breakers=True)
    primary, fb = Overloaded(responses=[OK]), Named(responses=[OK] * 10)
    for _ in range(3):  # trips the primary's breaker
        await ask(primary, fb)
    calls = primary.calls
    out = await ask(primary, fb)
    assert primary.calls == calls  # not called while open
    assert out.model == "kimi-k2.7-code-highspeed" and fb.calls == 4


async def test_non_transient_failure_does_not_use_fallback():
    timing.start(breakers=True)
    fb = Named(responses=[OK])
    with pytest.raises(ValueError):
        await ask(Broken(responses=[OK]), fb)
    assert fb.calls == 0


async def test_primary_answer_records_primary_model():
    timing.start(breakers=True)
    out = await ask(Named(responses=[OK], model_name="kimi-k3.1-pro-highspeed"), None)
    assert out.model == "kimi-k3.1-pro-highspeed"


def test_no_configured_fallback_outside_a_pipeline_run(monkeypatch):
    monkeypatch.setenv("LLM_SKEPTIC_FALLBACK_MODEL", "kimi-k2.7-code-highspeed")
    monkeypatch.setenv("LLM_SKEPTIC_PROVIDER", "moonshot")
    st.get_settings.cache_clear()
    timing._current.set(None)
    assert llm._fallback("skeptic") is None  # unit tests / plain calls
    timing.start()  # eval: timing without breakers
    assert llm._fallback("skeptic") is None
    assert llm._fallback("judge") is None


async def test_model_field_is_hidden_from_the_llm_and_never_taken_from_it():
    assert "model" not in json.dumps(ModelOpinion.model_json_schema())
    timing.start(breakers=True)
    forged = '{"verdict": "supported", "quote": "a b c d e", "rationale": "r", "model": "x"}'
    out = await ask(Named(responses=[forged], model_name="kimi-k3.1-pro-highspeed"), None)
    assert out.model == "kimi-k3.1-pro-highspeed"
