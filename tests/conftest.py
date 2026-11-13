import os

import pytest

# Live runs share the $1/day OpenAlex budget: cap paid searches per run.
os.environ.setdefault("OPENALEX_RUN_SEARCHES", "300")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE=1")
    for item in items:
        if "tests/live/" in str(item.fspath) or "live" in item.keywords:
            item.add_marker(skip)
