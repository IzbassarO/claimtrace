import os

import pytest

# Live runs share the $1/day OpenAlex budget: cap paid searches per run.
os.environ.setdefault("OPENALEX_RUN_SEARCHES", "300")


@pytest.fixture(autouse=True)
def _offline_cache(request, monkeypatch, tmp_path):
    """Offline tests never touch the real CACHE_DIR (source texts, OpenAlex answers, budget
    counter). Live tests keep the real one so paid searches count against the daily budget."""
    if "tests/live/" in str(request.node.fspath):
        return
    from claimtrace.core.settings import get_settings

    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE=1")
    for item in items:
        if "tests/live/" in str(item.fspath) or "live" in item.keywords:
            item.add_marker(skip)
