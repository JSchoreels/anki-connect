"""Prototype tests are independent of tests/conftest.py's Anki GUI fixtures."""

import pytest

import performance


@pytest.fixture
def namespace():
    # Each case gets fresh classes/settings; caches cannot leak between tests.
    return performance.load_code()
