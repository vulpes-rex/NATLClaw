"""Root conftest — ensure a fresh event loop is available for legacy tests
that use ``asyncio.get_event_loop().run_until_complete()``.

When ``asyncio.run()`` is called (e.g. in tests/unit/test_phase3.py), it
creates **and then closes** the default event loop.  Subsequent calls to
``asyncio.get_event_loop()`` in the same process will then raise
``RuntimeError: There is no current event loop in thread 'MainThread'``.

This autouse fixture creates a fresh loop before every test function and
sets it as the current loop, preventing cross-file pollution.
"""
import asyncio
import pytest


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Guarantee a usable asyncio event loop for every test."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    yield
