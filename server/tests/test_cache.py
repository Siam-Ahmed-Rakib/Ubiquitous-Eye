"""Tests for the result cache that fronts the heavy Sentinel Hub endpoints.

The cache stores a whole response keyed by analysis type + date + a quantised
bounding box, so re-running the same area skips the Sentinel Hub fetch entirely.
Everything here runs against a throwaway SQLite database; production points
DATABASE_URL at Supabase Postgres instead.
"""

import base64
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api_server
import cache
from test_classify import POLYGON, _stub_true_color_base, _synthetic_composite


@pytest.fixture
def sqlite_cache(tmp_path, monkeypatch):
    """Point the cache at a fresh SQLite file and reset its lazy engine."""
    db_path = tmp_path / "cache.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    cache.reset()
    yield
    cache.reset()


# ── Key building ──────────────────────────────────────────────────────────────

def test_quantize_collapses_near_identical_boxes():
    a = cache.make_key("classify", "2024-03", (90.00004, 23.69997, 90.10001, 23.80002))
    b = cache.make_key("classify", "2024-03", (90.0, 23.7, 90.1, 23.8))
    assert a == b, "sub-~50 m differences must round to the same key"

    c = cache.make_key("classify", "2024-03", (90.02, 23.7, 90.1, 23.8))
    assert c != b, "a real ~2 km shift must be a different key"

    # Analysis type and date are part of the identity.
    assert cache.make_key("analyze", "2024-03", (90.0, 23.7, 90.1, 23.8)) != b
    assert cache.make_key("classify", "2024-04", (90.0, 23.7, 90.1, 23.8)) != b


# ── Store / fetch ─────────────────────────────────────────────────────────────

def test_put_then_get_roundtrips(sqlite_cache):
    bbox = (90.0, 23.7, 90.1, 23.8)
    assert cache.cache_get("classify", "2024-03", bbox, is_current=False) is None

    cache.cache_put("classify", "2024-03", bbox, False, {"status": "success", "value": 42})

    got = cache.cache_get("classify", "2024-03", bbox, is_current=False)
    assert got is not None
    assert got["value"] == 42
    assert got["cached"] is True
    assert "cachedAt" in got


def test_put_overwrites_existing_entry(sqlite_cache):
    bbox = (90.0, 23.7, 90.1, 23.8)
    cache.cache_put("classify", "2024-03", bbox, False, {"v": 1})
    cache.cache_put("classify", "2024-03", bbox, False, {"v": 2})
    assert cache.cache_get("classify", "2024-03", bbox, is_current=False)["v"] == 2


def test_current_period_result_goes_stale(sqlite_cache):
    bbox = (90.0, 23.7, 90.1, 23.8)
    cache.cache_put("classify", "2026-07", bbox, True, {"status": "success"})

    # Fresh entry is still a hit.
    assert cache.cache_get("classify", "2026-07", bbox, is_current=True) is not None

    # Backdate it beyond the refresh window.
    stale = (
        datetime.now(timezone.utc) - timedelta(days=cache.CURRENT_PERIOD_TTL_DAYS + 1)
    ).isoformat()
    with cache._get_engine().begin() as conn:
        conn.execute(text(f"UPDATE {cache._TABLE} SET created_at = :ts"), {"ts": stale})

    # A current-period request now misses (forces a refresh); the same data as a
    # past-month request still hits, because past months never change.
    assert cache.cache_get("classify", "2026-07", bbox, is_current=True) is None
    assert cache.cache_get("classify", "2026-07", bbox, is_current=False) is not None


def test_disabled_cache_is_a_noop(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache.reset()
    bbox = (90.0, 23.7, 90.1, 23.8)
    # Neither call may raise; get is always a miss.
    cache.cache_put("classify", "2024-03", bbox, False, {"x": 1})
    assert cache.cache_get("classify", "2024-03", bbox, is_current=False) is None
    cache.reset()


# ── Endpoint integration ──────────────────────────────────────────────────────

@pytest.fixture
def counted_pipeline(monkeypatch):
    """Stub the Sentinel Hub pipeline and count how often it actually runs."""
    calls = {"pipeline": 0}

    class _Cfg:
        sh_client_id = "test-id"
        sh_client_secret = "test-secret"

    def _pipeline(polygon, year, month, cid, secret, **kwargs):
        calls["pipeline"] += 1
        return _synthetic_composite()

    monkeypatch.setattr(api_server, "build_config", lambda: _Cfg())
    monkeypatch.setattr(api_server, "run_composite_pipeline", _pipeline)
    monkeypatch.setattr(api_server, "fetch_true_color_base", _stub_true_color_base)
    return calls


def test_classify_second_request_is_served_from_cache(sqlite_cache, counted_pipeline):
    client = api_server.app.test_client()
    req = {"polygon": POLYGON, "year": 2024, "month": 3}

    first = client.post("/api/sentinel/classify", json=req)
    assert first.status_code == 200, first.get_data(as_text=True)
    assert counted_pipeline["pipeline"] == 1
    assert not first.get_json().get("cached", False)

    second = client.post("/api/sentinel/classify", json=req)
    assert second.status_code == 200
    # The slow Sentinel Hub path must NOT run again.
    assert counted_pipeline["pipeline"] == 1

    body = second.get_json()
    assert body["cached"] is True

    # Served response is the same substance as the freshly-computed one.
    fb = first.get_json()
    assert body["imagePngBase64"] == fb["imagePngBase64"]
    assert body["baseImagePngBase64"] == fb["baseImagePngBase64"]
    assert body["classes"] == fb["classes"]
    assert body["bounds"] == fb["bounds"]


def test_classify_distinct_area_is_not_a_cache_hit(sqlite_cache, counted_pipeline):
    client = api_server.app.test_client()
    client.post("/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3})
    assert counted_pipeline["pipeline"] == 1

    # Shift the polygon ~2 km east — a different area, so the pipeline must run.
    shifted = [[lon + 0.02, lat] for lon, lat in POLYGON]
    client.post("/api/sentinel/classify", json={"polygon": shifted, "year": 2024, "month": 3})
    assert counted_pipeline["pipeline"] == 2


def test_classify_without_database_always_computes(counted_pipeline, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    cache.reset()
    client = api_server.app.test_client()
    req = {"polygon": POLYGON, "year": 2024, "month": 3}

    client.post("/api/sentinel/classify", json=req)
    client.post("/api/sentinel/classify", json=req)
    # With no cache configured, both requests run the pipeline.
    assert counted_pipeline["pipeline"] == 2
    cache.reset()


# ── Phase 2: subset serving ───────────────────────────────────────────────────

def test_containing_returns_tightest_enclosing_entry(sqlite_cache):
    payload = {"status": "success"}
    # Two entries enclose the query; the medium one is the tighter fit.
    cache.cache_put("classify", "2024-03", (90.0, 23.7, 91.0, 24.7), False, payload, extras={"e": "big"})
    cache.cache_put("classify", "2024-03", (90.4, 23.9, 90.6, 24.1), False, payload, extras={"e": "medium"})

    got = cache.cache_get_containing("classify", "2024-03", (90.45, 23.95, 90.55, 24.05), is_current=False)
    assert got is not None
    assert got["extras"]["e"] == "medium", "should pick the tightest enclosing box"
    assert got["bounds"] == (90.4, 23.9, 90.6, 24.1)

    # Outside every entry -> no container.
    assert cache.cache_get_containing("classify", "2024-03", (80.0, 10.0, 80.1, 10.1), False) is None
    # Right area, wrong analysis type -> no container.
    assert cache.cache_get_containing("analyze", "2024-03", (90.45, 23.95, 90.55, 24.05), False) is None


def test_classify_subset_served_from_cached_larger_area(sqlite_cache, counted_pipeline):
    client = api_server.app.test_client()

    # Populate the cache with the full area.
    client.post("/api/sentinel/classify", json={"polygon": POLYGON, "year": 2024, "month": 3})
    assert counted_pipeline["pipeline"] == 1

    # A smaller polygon fully inside POLYGON's bbox is cropped from the cache.
    small = [[90.02, 23.72], [90.08, 23.72], [90.08, 23.78], [90.02, 23.78]]
    res = client.post("/api/sentinel/classify", json={"polygon": small, "year": 2024, "month": 3})

    assert res.status_code == 200, res.get_data(as_text=True)
    assert counted_pipeline["pipeline"] == 1, "subset must not trigger a Sentinel Hub fetch"
    body = res.get_json()
    assert body["cached"] is True
    assert body.get("cachedSubset") is True

    # Cropped bounds sit within the cached area and describe a smaller box.
    b = body["bounds"]
    assert 90.0 <= b["west"] < b["east"] <= 90.1
    assert 23.7 <= b["south"] < b["north"] <= 23.8
    assert body["stats"]["gridWidth"] <= 5 and body["stats"]["gridHeight"] <= 4

    # A valid overlay PNG comes back, sized to match the reported dimensions.
    img = Image.open(io.BytesIO(base64.b64decode(body["imagePngBase64"])))
    assert img.mode == "RGBA"
    assert (img.width, img.height) == (body["imageWidth"], body["imageHeight"])
