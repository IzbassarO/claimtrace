"""Live server 2026-11-14: every skeptic call ended in OpenAIRateLimitError (Moonshot 429) and a
6-claim job took 580 s (busy_llm_skeptic 1660 s over 9 calls). A 429 that says the account is out
of quota never clears by waiting, yet core.llm retries it like a burst limit (own retries on top of
the client's max_retries), so each claim waits minutes for a "judge only" result."""

import httpx
import openai
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from claimtrace.core import llm
from claimtrace.core import settings as st
from claimtrace.core.schemas import ModelOpinion


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(llm, "RATE_LIMIT_BACKOFF_S", 0.0)
    llm._limiters.clear()
    st.get_settings.cache_clear()
    yield
    llm._limiters.clear()
    st.get_settings.cache_clear()


class OutOfQuota(FakeListChatModel):
    calls: int = 0
    body: dict = {}

    async def ainvoke(self, *a, **k):
        self.calls += 1
        req = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
        raise openai.RateLimitError(
            "quota", response=httpx.Response(429, request=req), body=self.body
        )


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"type": "exceeded_current_quota_error", "message": "insufficient balance"}},
        {"error": {"type": "insufficient_quota", "code": "insufficient_quota"}},
    ],
)
async def test_quota_exhausted_429_is_not_retried(body):
    model = OutOfQuota(responses=["{}"], body=body)
    with pytest.raises(openai.RateLimitError):
        await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=model)
    assert model.calls == 1  # out of credit: fail fast, the claim becomes "judge only" at once


class Burst(OutOfQuota):
    async def ainvoke(self, *a, **k):
        self.calls += 1
        if self.calls == 1:
            req = httpx.Request("POST", "https://api.moonshot.ai/v1/chat/completions")
            raise openai.RateLimitError(
                "slow down", response=httpx.Response(429, request=req), body=self.body
            )
        return await FakeListChatModel.ainvoke(self, *a, **k)


async def test_burst_429_is_still_retried():
    ok = '{"verdict": "supported", "quote": "a b c d e", "rationale": "r"}'
    model = Burst(responses=[ok], body={"error": {"type": "rate_limit_reached_error"}})
    out = await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=model)
    assert out.verdict == "supported" and model.calls == 2
