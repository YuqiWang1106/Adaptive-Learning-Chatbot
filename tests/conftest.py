import os
import sys

import pytest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def pytest_collection_modifyitems(config, items) -> None:
    """Keep paid/destructive acceptance suites opt-in even on developer machines."""

    live_enabled = str(os.getenv("LEARNING_LIVE_TESTS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    load_enabled = str(os.getenv("LEARNING_LOAD_TESTS") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    skip_live = pytest.mark.skip(reason="set LEARNING_LIVE_TESTS=1 to run real staging tests")
    skip_load = pytest.mark.skip(reason="set LEARNING_LOAD_TESTS=1 to run data-creating load tests")
    for item in items:
        if "live" in item.keywords and not live_enabled:
            item.add_marker(skip_live)
        if "load" in item.keywords and not load_enabled:
            item.add_marker(skip_load)
