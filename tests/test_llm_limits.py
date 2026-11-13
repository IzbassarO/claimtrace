"""Per-role concurrency, 429 back-off to the base limit, token accounting."""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from claimtrace.core import llm, timing
from claimtrace.core import settings as st
from claimtrace.core.schemas import ModelOpinion

ANSWER = '{"verdict": "supported", "quote": "a b c d e"}'


@pytest.fixture(autouse=True)
def limits(monkeypatch):
    monkeypatch.setenv("LLM_CONCURRENCY", "2")
    monkeypatch.setenv("LLM_CONCURRENCY_SKEPTIC", "4")
    monkeypatch.setattr(llm, "RATE_LIMIT_BACKOFF_S", 0.0)
    llm._limiters.clear()
    st.get_settings.cache_clear()
    yield
    llm._limiters.clear()
    st.get_settings.cache_clear()


class Probe(FakeListChatModel):
    """Counts concurrent calls; can fail with 429 a few times."""

    active: int = 0
    peak: int = 0
    fail_429: int = 0

    async def ainvoke(self, *a, **k):
        if self.fail_429 > 0:
            self.fail_429 -= 1
            err = RuntimeError("rate limited")
            err.status_code = 429
            raise err
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        usage = {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}
        return AIMessage(ANSWER, usage_metadata=usage)


async def test_roles_have_their_own_limits():
    judge, skeptic = Probe(responses=[ANSWER]), Probe(responses=[ANSWER])
    await asyncio.gather(
        *[llm.ask_json("judge", "s", "u", ModelOpinion, model=judge) for _ in range(6)],
        *[llm.ask_json("skeptic", "s", "u", ModelOpinion, model=skeptic) for _ in range(8)],
    )
    assert judge.peak == 2 and skeptic.peak == 4


async def test_429_lowers_the_limit_and_retries_without_failing():
    model = Probe(responses=[ANSWER], fail_429=1)
    out = await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=model)
    assert out.verdict == "supported"
    assert llm._limiter("skeptic").limit == 2  # fell back to LLM_CONCURRENCY


async def test_persistent_429_raises_for_the_caller_to_degrade():
    model = Probe(responses=[ANSWER], fail_429=10)
    with pytest.raises(RuntimeError):
        await llm.ask_json("skeptic", "s", "u", ModelOpinion, model=model)


async def test_tokens_are_counted_per_role():
    t = timing.start()
    await llm.ask_json("judge", "s", "u", ModelOpinion, model=Probe(responses=[ANSWER]))
    d = t.as_dict()
    assert d["n_tokens_in_judge"] == 10 and d["n_tokens_out_judge"] == 3
