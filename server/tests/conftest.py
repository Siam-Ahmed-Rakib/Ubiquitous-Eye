"""Shared test fixtures.

The backend container carries a real ``DATABASE_URL`` (Supabase) in its
environment. Tests must never touch it — they would pollute the production cache
and, because results persist, interfere with one another. So the result cache is
disabled for every test by default; the tests that actually exercise caching
(see ``test_cache.py``) opt back in with their own throwaway SQLite database.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache


@pytest.fixture(autouse=True)
def _cache_off_by_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache.reset()
    yield
    cache.reset()
