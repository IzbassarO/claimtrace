import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE=1")
    for item in items:
        if "tests/live/" in str(item.fspath) or "live" in item.keywords:
            item.add_marker(skip)
