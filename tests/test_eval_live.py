"""Live checks for the eval harness (network). Run with RUN_LIVE=1."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from claimtrace.core.http import make_client  # noqa: E402
from eval import make_refs, run  # noqa: E402

pytestmark = pytest.mark.live


def test_scifact_download_and_load():
    dev, corpus = run.load_scifact(run.scifact_tarball())
    assert len(dev) == 300 and len(corpus) > 5000
    items = run.scifact_items(dev, corpus, 100, 0)
    assert len(items) == 100 and {i["gold"] for i in items} == {"supported", "unsupported", "nei"}


async def test_make_refs_crossref_lookup():
    async with make_client() as client:
        row = await make_refs.fetch(client, "10.1038/nature14539", want_retracted=False)
        ret = await make_refs.fetch(client, "10.1038/nature12968", want_retracted=True)
    assert row["title"] == "Deep learning" and row["year"] == 2015
    assert ret["retracted"] and not ret["title"].upper().startswith("RETRACTED")


async def test_retrieval_suite_on_real_haystacks():
    """Real SciFact haystacks, real fastembed: hybrid must actually embed (no silent fallback)."""
    res = await run.run_retrieval(3, 0)
    m = res["metrics"]
    assert m["n"] == 3 and m["haystack_chars_min"] >= 2500 and m["embed_errors"] == 0
    assert set(m["variants"]) >= {"bm25", "hybrid"}
