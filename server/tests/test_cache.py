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
from sqlalchemy import inspect, text

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


def test_row_has_surrogate_id_primary_key(sqlite_cache):
    """id is the primary key; cache_key stays unique so lookups are unaffected."""
    bbox = (90.0, 23.7, 90.1, 23.8)
    cache.cache_put("classify", "2024-03", bbox, False, {"v": 1})
    cache.cache_put("classify", "2024-04", bbox, False, {"v": 2})

    engine = cache._get_engine()
    pk = inspect(engine).get_pk_constraint(cache._TABLE)["constrained_columns"]
    assert pk == ["id"]

    # An inline UNIQUE surfaces as a constraint on SQLite and as a unique index
    # on Postgres, so accept either.
    insp = inspect(engine)
    uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints(cache._TABLE)}
    uniques |= {tuple(ix["column_names"]) for ix in insp.get_indexes(cache._TABLE) if ix["unique"]}
    assert ("cache_key",) in uniques

    with engine.connect() as conn:
        ids = [r[0] for r in conn.execute(text(f"SELECT id FROM {cache._TABLE} ORDER BY id"))]
    assert len(ids) == 2 and len(set(ids)) == 2, "each row gets its own id"

    # Overwriting an entry keeps the row count at one per cache_key.
    cache.cache_put("classify", "2024-03", bbox, False, {"v": 3})
    with engine.connect() as conn:
        n = conn.execute(text(f"SELECT COUNT(*) FROM {cache._TABLE}")).scalar()
    assert n == 2


def test_centre_longitude_latitude_are_stored(sqlite_cache):
    cache.cache_put("classify", "2024-03", (90.0, 23.7, 90.2, 23.9), False, {"v": 1})

    with cache._get_engine().connect() as conn:
        lon, lat = conn.execute(
            text(f"SELECT longitude, latitude FROM {cache._TABLE}")
        ).fetchone()

    assert lon == pytest.approx(90.1)
    assert lat == pytest.approx(23.8)


# ── Typed period columns ──────────────────────────────────────────────────────

def test_period_dates_parses_both_key_shapes():
    """date_key is a string because it holds one month *or* two; both parse."""
    from datetime import date

    assert cache._period_dates("2024-03") == (date(2024, 3, 1), None)
    assert cache._period_dates("2024-01_2025-01") == (date(2024, 1, 1), date(2025, 1, 1))

    # Unparseable input must not raise — the period columns are a convenience.
    assert cache._period_dates("not-a-date") == (None, None)
    assert cache._period_dates("") == (None, None)


def test_period_columns_are_stored_for_both_analyses(sqlite_cache):
    bbox = (90.0, 23.7, 90.2, 23.9)
    cache.cache_put("classify", "2024-03", bbox, False, {"v": 1})
    cache.cache_put("analyze_v4", "2024-01_2025-01", bbox, False, {"v": 2})

    with cache._get_engine().connect() as conn:
        rows = dict(conn.execute(text(
            f"SELECT analysis, period_start || '/' || COALESCE(period_end, '-') "
            f"FROM {cache._TABLE}"
        )).fetchall())

    # A single-month classify has no end; change detection carries both months.
    assert rows["classify"] == "2024-03-01/-"
    assert rows["analyze_v4"] == "2024-01-01/2025-01-01"


# ── Normalised classification rows ────────────────────────────────────────────

_CLASSES = [
    {"name": "Tree", "color": "#2E7D32", "pixels": 120, "percent": 60.0, "areaKm2": 1.2},
    {"name": "Water", "color": "#1565C0", "pixels": 80, "percent": 40.0, "areaKm2": 0.8},
]


def _class_rows(conn):
    """Summary rows joined back to their class names, ordered by share."""
    return conn.execute(text(
        f"SELECT k.name, k.color, r.pixel_count, r.percent, r.area_km2 "
        f"FROM {cache._CLASS_TABLE} r "
        f"JOIN {cache._LOOKUP_TABLE} k ON k.id = r.class_id "
        f"ORDER BY r.percent DESC"
    )).fetchall()


def test_classes_are_normalised_into_their_own_table(sqlite_cache):
    """The class breakdown becomes queryable rows, not just JSON in the payload."""
    cache.cache_put("classify", "2024-03", (90.0, 23.7, 90.2, 23.9), False,
                    {"status": "success", "classes": _CLASSES})

    engine = cache._get_engine()
    with engine.connect() as conn:
        rows = _class_rows(conn)

    assert [r[0] for r in rows] == ["Tree", "Water"]
    assert (rows[0][2], rows[0][3], rows[0][4]) == (120, 60.0, 1.2)

    # id is the primary key here too, and both foreign keys are real.
    insp = inspect(engine)
    assert insp.get_pk_constraint(cache._CLASS_TABLE)["constrained_columns"] == ["id"]
    refs = {
        fk["constrained_columns"][0]: fk["referred_table"]
        for fk in insp.get_foreign_keys(cache._CLASS_TABLE)
    }
    assert refs == {"cache_id": cache._TABLE, "class_id": cache._LOOKUP_TABLE}


def test_colour_is_stored_once_in_the_lookup_not_per_row(sqlite_cache):
    """The display colour is a constant of the class, so it lives in one place."""
    bbox = (90.0, 23.7, 90.2, 23.9)
    for month in ("2024-03", "2024-04", "2024-05"):
        cache.cache_put("classify", month, bbox, False,
                        {"status": "success", "classes": _CLASSES})

    with cache._get_engine().connect() as conn:
        # Three searches wrote six summary rows...
        assert conn.execute(
            text(f"SELECT COUNT(*) FROM {cache._CLASS_TABLE}")
        ).scalar() == 6
        # ...but the vocabulary still holds exactly two classes, one colour each.
        vocab = conn.execute(text(
            f"SELECT name, color FROM {cache._LOOKUP_TABLE} ORDER BY name"
        )).fetchall()
    assert vocab == [("Tree", "#2E7D32"), ("Water", "#1565C0")]

    # The summary table has no colour column at all to fall out of sync.
    cols = {c["name"] for c in inspect(cache._get_engine()).get_columns(cache._CLASS_TABLE)}
    assert "color" not in cols and "class_name" not in cols


def test_classes_are_replaced_when_an_entry_is_overwritten(sqlite_cache):
    bbox = (90.0, 23.7, 90.2, 23.9)
    cache.cache_put("classify", "2024-03", bbox, False,
                    {"status": "success", "classes": _CLASSES})
    cache.cache_put("classify", "2024-03", bbox, False, {"status": "success", "classes": [
        {"name": "Soil", "color": "#A1887F", "pixels": 200, "percent": 100.0, "areaKm2": 2.0},
    ]})

    with cache._get_engine().connect() as conn:
        names = [r[0] for r in _class_rows(conn)]
    assert names == ["Soil"], "stale child rows must not survive an overwrite"


# ── Per-location classification map ───────────────────────────────────────────

def _grid_extras(rows):
    """extras as classify writes them, from a grid of label strings."""
    table = ["", "Tree", "Crop", "Water", "Soil"]
    code = {name: i for i, name in enumerate(table)}
    return {
        "labels": [[code[v] for v in row] for row in rows],
        "gridW": len(rows[0]),
        "gridH": len(rows),
        "resM": 30,
        "codeToLabel": table,
    }


def _points(conn):
    return dict(conn.execute(text(
        f"SELECT p.longitude || ',' || p.latitude, k.name "
        f"FROM {cache._POINT_TABLE} p "
        f"JOIN {cache._LOOKUP_TABLE} k ON k.id = p.class_id"
    )).fetchall())


def test_points_are_stored_per_location_with_grid_orientation(sqlite_cache):
    """Row 0 of the grid is the NORTH edge, columns run west to east."""
    bbox = (90.0, 23.7, 90.2, 23.9)
    extras = _grid_extras([["Tree", "Water"],
                           ["Soil", "Crop"]])
    cache.cache_put("classify", "2024-03", bbox, False,
                    {"status": "success", "classes": _CLASSES}, extras=extras)

    with cache._get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT p.longitude, p.latitude, k.name FROM {cache._POINT_TABLE} p "
            f"JOIN {cache._LOOKUP_TABLE} k ON k.id = p.class_id"
        )).fetchall()

    assert len(rows) == 4
    by_name = {name: (lon, lat) for lon, lat, name in rows}
    # Tree is grid (0,0): north-west. Crop is (1,1): south-east.
    assert by_name["Tree"][1] > by_name["Soil"][1], "row 0 must be north"
    assert by_name["Tree"][0] < by_name["Water"][0], "column 0 must be west"
    assert by_name["Crop"][0] > by_name["Soil"][0]
    assert by_name["Crop"][1] < by_name["Water"][1]


def test_a_newer_observation_overwrites_a_location(sqlite_cache):
    """The user's scenario: 2025, then 2024, then 2026, arriving in that order."""
    bbox = (90.0, 23.7, 90.2, 23.9)
    put = lambda month, label: cache.cache_put(
        "classify", month, bbox, False,
        {"status": "success", "classes": _CLASSES},
        extras=_grid_extras([[label]]),
    )

    put("2025-06", "Tree")
    with cache._get_engine().connect() as conn:
        assert list(_points(conn).values()) == ["Tree"]

    # Older search must NOT win.
    put("2024-06", "Water")
    with cache._get_engine().connect() as conn:
        assert list(_points(conn).values()) == ["Tree"], "2024 must not overwrite 2025"

    # Newer search must win.
    put("2026-06", "Soil")
    with cache._get_engine().connect() as conn:
        rows = conn.execute(text(
            f"SELECT k.name, p.observed_on FROM {cache._POINT_TABLE} p "
            f"JOIN {cache._LOOKUP_TABLE} k ON k.id = p.class_id"
        )).fetchall()
    assert rows[0][0] == "Soil"
    assert str(rows[0][1]) == "2026-06-01"


def test_same_ground_from_differently_drawn_areas_is_one_row(sqlite_cache):
    """Snapping is what makes the overwrite rule fire at all.

    Cell centres come from each search's own bounding box, so two overlapping
    but differently-drawn selections land micrometres apart. Without snapping
    they would accumulate as separate rows and never overwrite.
    """
    a = (90.0, 23.7, 90.0027, 23.7027)
    b = (90.00001, 23.70001, 90.00271, 23.70271)  # ~1 m offset
    assert cache.snap_point(90.0013, 23.7013) == cache.snap_point(90.00131, 23.70131)

    cache.cache_put("classify", "2024-06", a, False,
                    {"status": "success", "classes": _CLASSES},
                    extras=_grid_extras([["Tree"]]))
    cache.cache_put("classify", "2025-06", b, False,
                    {"status": "success", "classes": _CLASSES},
                    extras=_grid_extras([["Water"]]))

    with cache._get_engine().connect() as conn:
        pts = _points(conn)
    assert len(pts) == 1, "the same ground must be one row, not two"
    assert list(pts.values()) == ["Water"], "the newer observation wins"


def test_unclassified_cells_are_not_stored(sqlite_cache):
    """Code 0 means the model had no cloud-free pixel; it must not overwrite."""
    cache.cache_put("classify", "2024-03", (90.0, 23.7, 90.2, 23.9), False,
                    {"status": "success", "classes": _CLASSES},
                    extras=_grid_extras([["Tree", ""], ["", "Water"]]))

    with cache._get_engine().connect() as conn:
        assert sorted(_points(conn).values()) == ["Tree", "Water"]


def test_points_survive_eviction_of_the_cached_response(sqlite_cache):
    """The map is ground truth, not a child of the payload that produced it."""
    bbox = (90.0, 23.7, 90.2, 23.9)
    cache.cache_put("classify", "2024-03", bbox, False,
                    {"status": "success", "classes": _CLASSES},
                    extras=_grid_extras([["Tree"]]))

    engine = cache._get_engine()
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {cache._TABLE}"))
    with engine.connect() as conn:
        assert list(_points(conn).values()) == ["Tree"], "evicting the cache must keep the map"


def test_payload_without_classes_writes_no_child_rows(sqlite_cache):
    """Change detection has no class breakdown; that is not an error."""
    cache.cache_put("analyze_v4", "2024-01_2025-01", (90.0, 23.7, 90.2, 23.9), False,
                    {"status": "success"})

    with cache._get_engine().connect() as conn:
        n = conn.execute(text(f"SELECT COUNT(*) FROM {cache._CLASS_TABLE}")).scalar()
    assert n == 0


def test_classes_are_backfilled_from_already_cached_payloads(tmp_path, monkeypatch):
    """A payload cached before the table existed still yields its class rows."""
    db_path = tmp_path / "backfill.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    cache.reset()
    try:
        cache.cache_put("classify", "2024-03", (90.0, 23.7, 90.2, 23.9), False,
                        {"status": "success", "classes": _CLASSES})
        # Drop the derived table, as if this database predated it.
        with cache._get_engine().begin() as conn:
            conn.execute(text(f"DROP TABLE {cache._CLASS_TABLE}"))
        cache.reset()

        # Re-opening recreates it and repopulates from the stored payload.
        cache.cache_get("classify", "2024-03", (90.0, 23.7, 90.2, 23.9), is_current=False)
        with cache._get_engine().connect() as conn:
            names = {r[0] for r in _class_rows(conn)}
        assert names == {"Tree", "Water"}
    finally:
        cache.reset()


def test_old_schema_is_migrated_in_place(tmp_path, monkeypatch):
    """A table written by the previous version keeps its rows and gains the new shape."""
    from sqlalchemy import create_engine

    db_path = tmp_path / "legacy.db"
    url = f"sqlite:///{db_path}"

    # Build the pre-migration table by hand and seed one row.
    legacy = create_engine(url, future=True)
    with legacy.begin() as conn:
        conn.execute(text(f"""
            CREATE TABLE {cache._TABLE} (
                cache_key   TEXT PRIMARY KEY,
                analysis    TEXT NOT NULL,
                date_key    TEXT NOT NULL,
                west        DOUBLE PRECISION NOT NULL,
                south       DOUBLE PRECISION NOT NULL,
                east        DOUBLE PRECISION NOT NULL,
                north       DOUBLE PRECISION NOT NULL,
                is_current  INTEGER NOT NULL DEFAULT 0,
                payload     TEXT NOT NULL,
                extras      TEXT,
                created_at  TEXT NOT NULL
            )
        """))
        conn.execute(text(
            f"CREATE INDEX idx_{cache._TABLE}_lookup ON {cache._TABLE} (analysis, date_key)"
        ))
        conn.execute(
            text(f"INSERT INTO {cache._TABLE} (cache_key, analysis, date_key, west, south, "
                 f"east, north, is_current, payload, extras, created_at) "
                 f"VALUES (:k, 'classify', '2024-03', 90.0, 23.7, 90.2, 23.9, 0, :p, NULL, :ts)"),
            {
                "k": cache.make_key("classify", "2024-03", (90.0, 23.7, 90.2, 23.9)),
                "p": '{"status":"success","value":7}',
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
    legacy.dispose()

    monkeypatch.setenv("DATABASE_URL", url)
    cache.reset()
    try:
        # Opening the cache migrates the table.
        got = cache.cache_get("classify", "2024-03", (90.0, 23.7, 90.2, 23.9), is_current=False)
        assert got is not None and got["value"] == 7, "existing rows survive the migration"

        engine = cache._get_engine()
        insp = inspect(engine)
        assert insp.get_pk_constraint(cache._TABLE)["constrained_columns"] == ["id"]
        cols = {c["name"] for c in insp.get_columns(cache._TABLE)}
        assert {"id", "longitude", "latitude", "period_start", "period_end"} <= cols

        # The identifier columns are bounded rather than unbounded TEXT.
        types = {c["name"]: str(c["type"]).upper() for c in insp.get_columns(cache._TABLE)}
        for name in ("cache_key", "analysis", "date_key"):
            assert "VARCHAR" in types[name], f"{name} should be bounded, got {types[name]}"

        # The legacy row's date_key is parsed into the typed period column.
        with engine.connect() as conn:
            start, end = conn.execute(
                text(f"SELECT period_start, period_end FROM {cache._TABLE}")
            ).fetchone()
        assert str(start) == "2024-03-01" and end is None

        # The legacy row is backfilled with its centre point.
        with engine.connect() as conn:
            lon, lat = conn.execute(
                text(f"SELECT longitude, latitude FROM {cache._TABLE}")
            ).fetchone()
        assert lon == pytest.approx(90.1) and lat == pytest.approx(23.8)

        # And the migrated table still accepts writes.
        cache.cache_put("classify", "2024-05", (90.0, 23.7, 90.2, 23.9), False, {"v": 9})
        assert cache.cache_get("classify", "2024-05", (90.0, 23.7, 90.2, 23.9), False)["v"] == 9
    finally:
        cache.reset()


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
