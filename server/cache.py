"""
Result cache for the heavy Sentinel Hub + model endpoints.

The slow part of a `/classify` or `/analyze` request is the *per-area* Sentinel
Hub fetch — a whole-month composite downloads one scene per available date
(~10-15 network round-trips) before the model ever runs. The model itself is
cheap. So we cache the **whole response** keyed by analysis type + date(s) + a
coarsely-quantised bounding box. Re-running the same (or an almost-identical)
area then returns the stored response with zero Sentinel Hub calls.

Storage is any SQLAlchemy-supported database, chosen by the ``DATABASE_URL``
environment variable. In production that is Supabase Postgres; the tests point
it at a throwaway SQLite file. If ``DATABASE_URL`` is unset or the database is
unreachable, every operation degrades to a silent no-op, so the endpoints keep
working exactly as before — the cache can never be the reason a request fails.

Rows are identified by a surrogate ``id`` primary key; the natural key
(``cache_key``) is kept as a UNIQUE column so lookups are unchanged while
foreign keys and admin tooling get a stable, opaque row identity. On Postgres
``payload``/``extras`` are real JSONB columns, so stored responses are queryable
from SQL instead of being opaque text. An existing table created by an earlier
version is migrated in place on startup — see ``_migrate_postgres`` /
``_migrate_sqlite``.

Columns are declared with the narrowest type that fits the data: bounded
``VARCHAR`` for the short identifier strings, ``DATE`` for the analysed period,
``TIMESTAMPTZ`` for ``created_at``, ``BOOLEAN`` for the flag. (On Postgres
``TEXT`` and ``VARCHAR(n)`` are the same varlena type and perform identically —
the length bound is there as a data-integrity constraint, not an optimisation.
``DATE``/``TIMESTAMPTZ``/``BOOLEAN`` genuinely are smaller and comparable in SQL,
which ``TEXT`` was not.)

``date_key`` stays a string because it is not a date: classify writes
``"2024-03"`` and change detection writes ``"2024-01_2025-01"``, a *pair* of
months that no DATE column can hold. The parsed form lives alongside it in
``period_start``/``period_end`` (end is NULL for a single-month analysis), so
date arithmetic and range queries are still possible from SQL.

Per-class classification output is normalised out of the JSON payload into
``classification_result`` — one row per land-cover class per analysed area,
carrying the area's centre point. That makes "what was found at this
longitude/latitude" an indexed SQL query instead of a JSON scan.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone

logger = logging.getLogger("capstone.cache")

# Round lat/lon to this many decimals when building the key, so selections that
# differ by less than ~100 m collapse to the same cache entry. At ~23 N,
# 0.001 deg is ~111 m of latitude and ~102 m of longitude.
_BBOX_QUANT = 3

# Results for the *current* month are re-fetched once the cached copy is older
# than this, so scenes that land later in the month get picked up. Results for
# past months never change, so they are served from cache indefinitely.
CURRENT_PERIOD_TTL_DAYS = 5

_TABLE = "analysis_cache"

# Per-class rows lifted out of a classify payload, one per land-cover class.
_CLASS_TABLE = "classification_result"

# The land-cover vocabulary. Four rows, ever — so the name and its display
# colour are stored once here instead of being repeated on every result row.
_LOOKUP_TABLE = "land_cover_class"

# One row per ~30 m ground location, holding the most recent classification
# known for it. Not a per-search log: see _store_points.
_POINT_TABLE = "classification_point"

# Point coordinates are snapped to this grid before storing. Cell centres are
# derived from each search's own bounding box, so two searches covering the same
# ground from differently-drawn polygons land a few metres apart and would never
# compare equal as floats. Snapping to a fixed global grid makes the same ground
# yield the same key regardless of how the area was selected. 0.00027 deg is
# ~30 m, the finest resolution the classifier produces.
_POINT_GRID_DEG = 0.00027

# Rows per INSERT when writing the point map, which cuts a 20,000-cell grid from
# thousands of round trips to a few dozen. SQLite historically caps a statement
# at 999 parameters, so 300 rows x 3 per-row parameters is the safe ceiling
# there; Postgres allows 65535, and bigger batches matter more against a remote
# database where each statement also costs a round trip.
_POINT_CHUNK_SQLITE = 300
_POINT_CHUNK_PG = 1000

# Mirrors ``_CODE_TO_LABEL`` in api_server.py, used to decode the stored label
# grid. New writes carry their own copy in ``extras["codeToLabel"]`` and that is
# preferred; this fallback only serves entries cached before that was added.
_FALLBACK_CODE_TO_LABEL = ["", "Tree", "Crop", "Water", "Soil"]

# Longest cache_key we can produce: analysis (<=16) + date_key (<=16) + four
# 3-decimal coordinates, plus separators. ~60 chars in practice; 160 is headroom
# that still bounds the column.
_CACHE_KEY_LEN = 160

# Columns carried over verbatim when an old SQLite table has to be rebuilt.
_CARRIED_COLUMNS = (
    "cache_key", "analysis", "date_key",
    "west", "south", "east", "north",
    "is_current", "payload", "extras", "created_at",
)

# Lazily-built SQLAlchemy engine. ``_engine_ready`` records that we already tried
# (so a failed/absent DB is not retried on every request).
_engine = None
_engine_ready = False

# True once we have confirmed ``payload``/``extras`` really are JSONB columns.
# Writes must then cast the JSON text explicitly (Postgres has no implicit
# text -> jsonb assignment cast) and reads get back parsed objects, not strings.
_json_columns = False

# Same idea for the temporal columns. Values are always *bound* as ISO-8601
# strings -- Python 3.12 deprecated sqlite3's implicit date/datetime adapters,
# so passing native objects through raw SQL is a dead end -- and cast to the
# real type in SQL when the column has actually been migrated. A database still
# on the old TEXT schema keeps working: no cast is emitted, the string is stored
# verbatim, exactly as before.
_ts_column = False
_date_columns = False

# Dialect of the live engine. The derived tables are created by this module, so
# unlike the columns above their types are known rather than reflected -- but
# the casts still have to be emitted only on Postgres.
_pg_dialect = False


# ── Key building ──────────────────────────────────────────────────────────────

def quantize_bbox(west: float, south: float, east: float, north: float):
    """Snap a bounding box to the ~100 m key grid."""
    def q(v: float) -> float:
        return round(float(v), _BBOX_QUANT)
    return q(west), q(south), q(east), q(north)


def make_key(analysis: str, date_key: str, bbox) -> str:
    """Canonical cache key: analysis type, date(s), and the quantised bbox.

    ``date_key`` is caller-defined — ``"2024-03"`` for a single-month classify,
    ``"2024-01_2025-01"`` for a two-date change-detection run.
    """
    qw, qs, qe, qn = quantize_bbox(*bbox)
    return f"{analysis}|{date_key}|{qw:.3f},{qs:.3f},{qe:.3f},{qn:.3f}"


# ── Engine / schema ───────────────────────────────────────────────────────────

def _database_url() -> str | None:
    """Read DATABASE_URL and normalise it to an explicit SQLAlchemy driver.

    Supabase/Heroku hand out ``postgres://`` or ``postgresql://``; SQLAlchemy
    needs the driver spelled out (``postgresql+psycopg2://``). SQLite URLs are
    left untouched.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return None
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def _is_postgres(engine) -> bool:
    return engine.dialect.name.startswith("postgres")


def _create_table_sql(pg: bool) -> str:
    """DDL for a fresh cache table.

    ``id`` is the primary key — an opaque surrogate that stays valid even if the
    natural key ever has to change, and the row identity
    ``classification_result`` points at; ``cache_key`` keeps its uniqueness so
    the lookups below are unaffected. ``longitude``/``latitude`` are the centre
    of the analysed box, denormalised so a row can be located on a map (or
    matched against a user's position) without re-deriving it from the four
    bounds. DOUBLE PRECISION carries the real bbox for the "sub-area fully
    inside a computed one" lookup.

    ``period_start``/``period_end`` are the parsed form of ``date_key`` (see the
    module docstring). Both stay nullable: a row whose ``date_key`` cannot be
    parsed must still be cacheable, and on an in-place migration a single
    unparseable legacy value must not abort the whole transaction.

    Every type here is chosen per dialect. SQLite has no DATE, TIMESTAMPTZ,
    BOOLEAN or JSONB storage class, and declaring one is not harmless — a DATE
    column takes NUMERIC affinity, which would silently truncate an ISO date.
    So SQLite gets TEXT/INTEGER and Postgres gets the real types; only the test
    backend is affected.
    """
    id_col = "id BIGSERIAL PRIMARY KEY" if pg else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    json_type = "JSONB" if pg else "TEXT"
    ts_type = "TIMESTAMPTZ" if pg else "TEXT"
    bool_type = "BOOLEAN" if pg else "INTEGER"
    # SQLite maps a DATE column to NUMERIC affinity, which truncates an ISO
    # date at the first dash ('2024-03-01' -> 2024). TEXT is the correct SQLite
    # storage for dates, and keeps ISO ordering intact.
    date_type = "DATE" if pg else "TEXT"
    return f"""
    CREATE TABLE IF NOT EXISTS {_TABLE} (
        {id_col},
        cache_key    VARCHAR({_CACHE_KEY_LEN}) NOT NULL,
        analysis     VARCHAR(32) NOT NULL,
        date_key     VARCHAR(32) NOT NULL,
        period_start {date_type},
        period_end   {date_type},
        west         DOUBLE PRECISION NOT NULL,
        south        DOUBLE PRECISION NOT NULL,
        east         DOUBLE PRECISION NOT NULL,
        north        DOUBLE PRECISION NOT NULL,
        longitude    DOUBLE PRECISION,
        latitude     DOUBLE PRECISION,
        is_current   {bool_type} NOT NULL DEFAULT FALSE,
        payload      {json_type} NOT NULL,
        extras       {json_type},
        created_at   {ts_type} NOT NULL,
        -- Named table-level constraint rather than an inline UNIQUE: it is
        -- easier to drop/recreate by name, and SQLAlchemy's SQLite reflection
        -- only reports table-level constraints once a column type carries
        -- parentheses, which VARCHAR(n) does.
        CONSTRAINT uq_{_TABLE}_cache_key UNIQUE (cache_key)
    )
    """


def _create_lookup_table_sql(pg: bool) -> str:
    """DDL for the land-cover vocabulary.

    Four rows for the life of the project (Tree/Crop/Water/Soil). The display
    colour belongs here and nowhere else: it is a constant of the class, so
    holding it on every result row would repeat the same seven characters
    thousands of times. Rows referencing a class carry a SMALLINT id instead,
    which is what makes the per-point table affordable.
    """
    id_col = "id SMALLSERIAL PRIMARY KEY" if pg else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
    CREATE TABLE IF NOT EXISTS {_LOOKUP_TABLE} (
        {id_col},
        name  VARCHAR(32) NOT NULL,
        color VARCHAR(7),
        CONSTRAINT uq_{_LOOKUP_TABLE}_name UNIQUE (name)
    )
    """


def _create_class_table_sql(pg: bool) -> str:
    """DDL for the per-class summary of one analysed area.

    One row per land-cover class per search — four rows for a typical classify
    — so this stays small even though the parent payload is large. The breakdown
    already exists inside ``payload``; holding it here as columns is what makes
    it queryable.

    No coordinates: the analysed area's centre is already on the parent row, so
    repeating it here would be duplication. Join to ``analysis_cache`` for
    location, or use ``classification_point`` for real per-location data.
    """
    id_col = "id BIGSERIAL PRIMARY KEY" if pg else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
    CREATE TABLE IF NOT EXISTS {_CLASS_TABLE} (
        {id_col},
        cache_id    BIGINT NOT NULL
                    REFERENCES {_TABLE} (id) ON DELETE CASCADE,
        class_id    SMALLINT NOT NULL
                    REFERENCES {_LOOKUP_TABLE} (id),
        pixel_count INTEGER NOT NULL DEFAULT 0,
        percent     DOUBLE PRECISION NOT NULL DEFAULT 0,
        area_km2    DOUBLE PRECISION NOT NULL DEFAULT 0,
        CONSTRAINT uq_{_CLASS_TABLE}_cache_class UNIQUE (cache_id, class_id)
    )
    """


def _create_point_table_sql(pg: bool) -> str:
    """DDL for the per-location classification map.

    Unlike the tables above this is **not** a log of searches — it is the
    current best answer for each piece of ground. One row per snapped ~30 m
    location, holding the classification from the most recently *observed*
    period seen for it. Searching June 2024 after someone searched June 2025
    leaves the row alone; searching June 2026 replaces it. See ``_store_points``.

    ``observed_on`` is the analysed period (``period_start``), not the time the
    row was written — that distinction is the whole point. ``updated_at`` keeps
    the write time separately for auditing.

    ``cache_id`` records which search last wrote the row, but uses ON DELETE SET
    NULL rather than CASCADE: evicting a cached response must not delete the
    map, because the classification remains true after the cached PNG is gone.
    """
    id_col = "id BIGSERIAL PRIMARY KEY" if pg else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    date_type = "DATE" if pg else "TEXT"
    ts_type = "TIMESTAMPTZ" if pg else "TEXT"
    return f"""
    CREATE TABLE IF NOT EXISTS {_POINT_TABLE} (
        {id_col},
        longitude   DOUBLE PRECISION NOT NULL,
        latitude    DOUBLE PRECISION NOT NULL,
        class_id    SMALLINT NOT NULL
                    REFERENCES {_LOOKUP_TABLE} (id),
        observed_on {date_type} NOT NULL,
        cache_id    BIGINT
                    REFERENCES {_TABLE} (id) ON DELETE SET NULL,
        updated_at  {ts_type} NOT NULL,
        CONSTRAINT uq_{_POINT_TABLE}_location UNIQUE (longitude, latitude)
    )
    """


def _index_sqls() -> list[str]:
    """Indexes worth their write cost on these two tables.

    ``lookup`` serves ``cache_get_containing``'s WHERE clause. ``centre`` and the
    two on the child table serve the map/point queries the denormalised
    coordinates exist for. ``cache_id`` is indexed explicitly because Postgres
    does *not* create an index for a foreign key automatically, and the cascade
    delete would otherwise scan the whole child table.
    """
    return [
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_lookup "
        f"ON {_TABLE} (analysis, date_key)",
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_period "
        f"ON {_TABLE} (period_start, period_end)",
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_centre "
        f"ON {_TABLE} (longitude, latitude)",
        f"CREATE INDEX IF NOT EXISTS idx_{_CLASS_TABLE}_cache "
        f"ON {_CLASS_TABLE} (cache_id)",
        f"CREATE INDEX IF NOT EXISTS idx_{_CLASS_TABLE}_class "
        f"ON {_CLASS_TABLE} (class_id)",
        # The UNIQUE (longitude, latitude) constraint already indexes location
        # lookups, so only the other two access paths need one of their own.
        f"CREATE INDEX IF NOT EXISTS idx_{_POINT_TABLE}_class "
        f"ON {_POINT_TABLE} (class_id)",
        f"CREATE INDEX IF NOT EXISTS idx_{_POINT_TABLE}_observed "
        f"ON {_POINT_TABLE} (observed_on)",
    ]


def snap_point(lon: float, lat: float) -> tuple[float, float]:
    """Snap a coordinate to the shared ~30 m point grid.

    Rounded to 8 decimals afterwards so the same ground always produces a
    bit-identical float, which is what the UNIQUE constraint compares.
    """
    return (
        round(round(float(lon) / _POINT_GRID_DEG) * _POINT_GRID_DEG, 8),
        round(round(float(lat) / _POINT_GRID_DEG) * _POINT_GRID_DEG, 8),
    )


def _grid_points(extras, bounds):
    """Yield ``(lon, lat, label)`` for every classified cell in a stored grid.

    ``extras`` is what classify wrote: nested integer codes plus the grid size.
    ``bounds`` is the parent row's ``(west, south, east, north)``. Cells run west
    to east across columns and **north to south** down rows, matching
    ``_crop_classify`` in api_server.py.

    Cells the model could not classify (code 0, an empty label) are skipped —
    storing "unknown" would overwrite a real observation from another search.
    """
    if not isinstance(extras, dict):
        return
    codes = extras.get("labels")
    if not isinstance(codes, list) or not codes:
        return

    table = extras.get("codeToLabel") or _FALLBACK_CODE_TO_LABEL
    west, south, east, north = (float(v) for v in bounds)
    height = len(codes)
    width = len(codes[0]) if isinstance(codes[0], list) else 0
    if not width or not height:
        return

    lon_span = (east - west) or 1e-9
    lat_span = (north - south) or 1e-9

    for r, row in enumerate(codes):
        lat = north - (r + 0.5) * lat_span / height
        for c, code in enumerate(row):
            try:
                label = table[int(code)]
            except (IndexError, TypeError, ValueError):
                continue
            if not label:
                continue
            lon = west + (c + 0.5) * lon_span / width
            yield lon, lat, label


def _period_dates(date_key: str):
    """Parse a ``date_key`` into (period_start, period_end).

    ``"2024-03"`` -> (date(2024, 3, 1), None) — a single month.
    ``"2024-01_2025-01"`` -> (date(2024, 1, 1), date(2025, 1, 1)) — a comparison.

    Both are normalised to the first of the month, which is what the analysis
    actually covers: a whole-month composite, not a specific day. Anything
    unrecognised yields (None, None) rather than raising — the period columns
    are a reporting convenience and must never be the reason a write fails.
    """
    def month_start(part: str):
        year, month = part.split("-")
        return date(int(year), int(month), 1)

    parts = str(date_key).split("_")
    try:
        start = month_start(parts[0])
        end = month_start(parts[1]) if len(parts) > 1 else None
        return start, end
    except (ValueError, IndexError):
        return None, None


def _backfill_periods(conn) -> int:
    """Fill period_start/period_end for rows migrated from the old schema.

    Done in Python rather than SQL so one implementation covers both dialects
    (SQLite has no split_part/to_date). Returns the number of rows updated.
    """
    from sqlalchemy import text

    rows = conn.execute(text(
        f"SELECT id, date_key FROM {_TABLE} WHERE period_start IS NULL"
    )).fetchall()

    updated = 0
    for row_id, date_key in rows:
        start, end = _period_dates(date_key)
        if start is None:
            continue
        conn.execute(
            text(f"UPDATE {_TABLE} SET period_start = :s, period_end = :e WHERE id = :i"),
            {"s": start, "e": end, "i": row_id},
        )
        updated += 1
    return updated


def _migrate_postgres(engine) -> None:
    """Bring an existing Postgres table up to the current schema, in place.

    Every step is conditional on what the table actually looks like, so this is
    a no-op once migrated and safe to run on every boot. It all happens in one
    transaction (Postgres DDL is transactional), so a failure part-way leaves
    the old, working schema untouched.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns(_TABLE)}
    pk = insp.get_pk_constraint(_TABLE) or {}
    pk_cols = list(pk.get("constrained_columns") or [])
    uniques = {tuple(u.get("column_names") or []) for u in insp.get_unique_constraints(_TABLE)}
    uniques |= {
        tuple(ix.get("column_names") or [])
        for ix in insp.get_indexes(_TABLE) if ix.get("unique")
    }

    stmts: list[str] = []

    # Task 3: the analysed centre point.
    if "longitude" not in cols:
        stmts.append(f"ALTER TABLE {_TABLE} ADD COLUMN longitude DOUBLE PRECISION")
    if "latitude" not in cols:
        stmts.append(f"ALTER TABLE {_TABLE} ADD COLUMN latitude DOUBLE PRECISION")

    # Task 2: text -> jsonb. Everything ever written here came from json.dumps,
    # so the USING cast cannot see malformed input.
    for name in ("payload", "extras"):
        col = cols.get(name)
        if col is not None and "JSON" not in str(col["type"]).upper():
            stmts.append(
                f"ALTER TABLE {_TABLE} ALTER COLUMN {name} TYPE JSONB USING {name}::jsonb"
            )

    # Task 1: surrogate id becomes the primary key, cache_key stays unique.
    if "id" not in cols:
        stmts.append(f"ALTER TABLE {_TABLE} ADD COLUMN id BIGSERIAL")
    if pk_cols != ["id"]:
        if ("cache_key",) not in uniques:
            stmts.append(
                f"ALTER TABLE {_TABLE} "
                f"ADD CONSTRAINT {_TABLE}_cache_key_key UNIQUE (cache_key)"
            )
        if pk_cols:
            # Drop before add: a table can only carry one primary key.
            stmts.append(f'ALTER TABLE {_TABLE} DROP CONSTRAINT "{pk["name"]}"')
        stmts.append(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_TABLE}_pkey PRIMARY KEY (id)")

    # Task 4: narrow the identifier columns from unbounded TEXT. In Postgres this
    # is a constraint change, not a performance one -- the two types are stored
    # identically -- so it is safe on populated data as long as nothing exceeds
    # the bound, which the key builder guarantees.
    for name, length in (
        ("cache_key", _CACHE_KEY_LEN), ("analysis", 32), ("date_key", 32),
    ):
        col = cols.get(name)
        if col is not None and "VARCHAR" not in str(col["type"]).upper():
            stmts.append(
                f"ALTER TABLE {_TABLE} ALTER COLUMN {name} TYPE VARCHAR({length})"
            )

    # Task 5: real DATE columns for the analysed period. date_key itself stays a
    # string because a change-detection key holds *two* months.
    for name in ("period_start", "period_end"):
        if name not in cols:
            stmts.append(f"ALTER TABLE {_TABLE} ADD COLUMN {name} DATE")

    # Task 6: created_at was an ISO-8601 string; every value was written by
    # datetime.isoformat(), so the cast is total.
    created = cols.get("created_at")
    if created is not None and "TIMESTAMP" not in str(created["type"]).upper():
        stmts.append(
            f"ALTER TABLE {_TABLE} ALTER COLUMN created_at "
            f"TYPE TIMESTAMPTZ USING created_at::timestamptz"
        )

    # Task 7: is_current is a flag, not a number.
    flag = cols.get("is_current")
    if flag is not None and "BOOL" not in str(flag["type"]).upper():
        stmts.append(
            f"ALTER TABLE {_TABLE} ALTER COLUMN is_current DROP DEFAULT"
        )
        stmts.append(
            f"ALTER TABLE {_TABLE} ALTER COLUMN is_current "
            f"TYPE BOOLEAN USING (is_current <> 0)"
        )
        stmts.append(
            f"ALTER TABLE {_TABLE} ALTER COLUMN is_current SET DEFAULT FALSE"
        )

    if not stmts:
        return

    # Backfill the centre for rows written before those columns existed.
    stmts.append(
        f"UPDATE {_TABLE} SET longitude = (west + east) / 2.0, "
        f"latitude = (south + north) / 2.0 "
        f"WHERE longitude IS NULL OR latitude IS NULL"
    )

    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))
        # Needs the period columns to exist, so it runs after the DDL above --
        # same transaction, so a failure rolls the whole migration back.
        _backfill_periods(conn)
    logger.info("Result cache schema migrated (%d statement(s))", len(stmts))


def _migrate_sqlite(engine) -> None:
    """Bring an existing SQLite table up to the current schema.

    SQLite cannot ALTER a primary key, so the table is rebuilt and the rows
    copied across. Cheap here — SQLite is only ever the test/local backend.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    cols = [c["name"] for c in insp.get_columns(_TABLE)]
    pk_cols = list((insp.get_pk_constraint(_TABLE) or {}).get("constrained_columns") or [])
    if pk_cols == ["id"] and {"longitude", "latitude", "period_start"} <= set(cols):
        return

    carried = [c for c in _CARRIED_COLUMNS if c in cols]
    lon = "longitude" if "longitude" in cols else "(west + east) / 2.0"
    lat = "latitude" if "latitude" in cols else "(south + north) / 2.0"
    old = f"{_TABLE}_old"

    with engine.begin() as conn:
        # Both derived tables reference ids in the table about to be dropped,
        # and a rebuild renumbers them. They are regenerated from the payloads
        # by _backfill_derived, so discard rather than remap.
        conn.execute(text(f"DROP TABLE IF EXISTS {_CLASS_TABLE}"))
        conn.execute(text(f"DROP TABLE IF EXISTS {_POINT_TABLE}"))
        conn.execute(text(f"DROP TABLE IF EXISTS {old}"))
        conn.execute(text(f"ALTER TABLE {_TABLE} RENAME TO {old}"))
        conn.execute(text(_create_table_sql(pg=False)))
        conn.execute(text(
            f"INSERT INTO {_TABLE} ({', '.join(carried)}, longitude, latitude) "
            f"SELECT {', '.join(carried)}, {lon}, {lat} FROM {old}"
        ))
        # Drops the old table's indexes too, freeing the shared index name.
        conn.execute(text(f"DROP TABLE {old}"))
        _backfill_periods(conn)
    logger.info("Result cache schema rebuilt (SQLite)")


def _backfill_derived(engine):
    """Populate both derived tables from payloads already in the cache.

    Without this they stay empty until every cached classify is recomputed — the
    data is already stored, just locked inside the JSON.

    Rows are processed oldest period first so the point map converges on the
    same answer it would have reached had the searches been replayed in order.
    The conditional upsert makes that ordering belt-and-braces rather than load
    bearing, but it keeps the write count down.

    On Postgres only the sub-documents actually needed are selected, never the
    whole payload: a classify response carries two base64 PNGs, so pulling full
    documents for every row would move tens of megabytes to extract a few
    hundred bytes of class breakdown. SQLite has no such projection, but it is
    the test backend with tiny rows.
    """
    from sqlalchemy import text

    pg = _is_postgres(engine)
    missing = f"NOT EXISTS (SELECT 1 FROM {_CLASS_TABLE} c WHERE c.cache_id = a.id)"
    order = "ORDER BY a.period_start NULLS FIRST" if pg else "ORDER BY a.period_start"
    if pg:
        sql = (
            f"SELECT a.id, a.longitude, a.latitude, a.payload->'classes', "
            f"       a.extras, a.west, a.south, a.east, a.north, a.period_start "
            f"FROM {_TABLE} a "
            f"WHERE jsonb_typeof(a.payload->'classes') = 'array' AND {missing} {order}"
        )
    else:
        sql = (
            f"SELECT a.id, a.longitude, a.latitude, a.payload, "
            f"       a.extras, a.west, a.south, a.east, a.north, a.period_start "
            f"FROM {_TABLE} a WHERE {missing} {order}"
        )

    summaries = points = 0
    with engine.begin() as conn:
        rows = conn.execute(text(sql)).fetchall()
        for row in rows:
            (row_id, lon, lat, blob, extras_blob,
             west, south, east, north, period_start) = row
            classes = _json_load(blob)
            if not pg:  # whole payload came back; dig out the array
                classes = classes.get("classes") if isinstance(classes, dict) else None
            if not isinstance(classes, list) or not classes:
                continue

            summaries += _insert_classes(
                conn, row_id, classes,
                lon if lon is not None else 0.0,
                lat if lat is not None else 0.0,
            )

            observed = _as_date(period_start)
            if observed is not None and None not in (west, south, east, north):
                points += _store_points(
                    conn, row_id, _json_load(extras_blob),
                    (west, south, east, north), observed,
                )
    return summaries, points


def _as_date(value):
    """Coerce a stored period value to a ``date``.

    Postgres hands back a real date; SQLite stores the ISO string it was given.
    """
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _init_schema(engine) -> None:
    """Create the table, or migrate an older one, then record the column types."""
    global _json_columns
    from sqlalchemy import inspect, text

    pg = _is_postgres(engine)
    if inspect(engine).has_table(_TABLE):
        # A migration that cannot run must not take the cache down with it: the
        # old schema still serves reads and writes, so log and carry on.
        try:
            if pg:
                _migrate_postgres(engine)
            elif engine.dialect.name == "sqlite":
                _migrate_sqlite(engine)
        except Exception as exc:  # pragma: no cover - depends on external DB
            logger.warning("Result cache schema migration failed (%s) — using existing schema", exc)
    else:
        with engine.begin() as conn:
            conn.execute(text(_create_table_sql(pg)))

    # The derived tables are additive: created whenever missing, including on a
    # database whose parent table predates them. An earlier classification_result
    # kept the class name and colour on every row; it is pure derived data, so
    # the cheapest correct migration is to drop it and let the backfill below
    # rebuild it against the lookup table.
    with engine.begin() as conn:
        if inspect(engine).has_table(_CLASS_TABLE):
            existing = {c["name"] for c in inspect(engine).get_columns(_CLASS_TABLE)}
            if "class_id" not in existing:
                conn.execute(text(f"DROP TABLE {_CLASS_TABLE}"))
        conn.execute(text(_create_lookup_table_sql(pg)))
        conn.execute(text(_create_class_table_sql(pg)))
        conn.execute(text(_create_point_table_sql(pg)))

    # Indexes are created separately from the tables so that a failure to add
    # one (e.g. a name already taken by an old schema) cannot cost us the table.
    for stmt in _index_sqls():
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
        except Exception as exc:  # pragma: no cover - depends on external DB
            logger.warning("Could not create index (%s)", exc)

    # Whether writes need an explicit cast is read back from the database rather
    # than assumed, so a migration that could not run still leaves a working
    # (text-column) cache instead of failing every insert. This must settle
    # before the backfill below, which writes through the same code path.
    global _ts_column, _date_columns, _pg_dialect
    types = {
        c["name"]: str(c["type"]).upper()
        for c in inspect(engine).get_columns(_TABLE)
    }
    _json_columns = "JSON" in types.get("payload", "")
    _ts_column = "TIMESTAMP" in types.get("created_at", "")
    _date_columns = "DATE" in types.get("period_start", "")
    _pg_dialect = pg

    # Lift the class breakdown and the point map out of payloads cached before
    # these tables existed. Purely derived data, so a failure here costs nothing
    # but an emptier table.
    try:
        summaries, points = _backfill_derived(engine)
        if summaries or points:
            logger.info(
                "Backfilled %d class row(s) and %d point(s) from cached payloads",
                summaries, points,
            )
    except Exception as exc:  # pragma: no cover - depends on external DB
        logger.warning("Derived-table backfill skipped (%s)", exc)


def _get_engine():
    """Build (once) and return the SQLAlchemy engine, or None if unavailable."""
    global _engine, _engine_ready
    if _engine_ready:
        return _engine
    _engine_ready = True

    url = _database_url()
    if not url:
        logger.info("DATABASE_URL not set — result cache disabled")
        _engine = None
        return None

    try:
        from sqlalchemy import create_engine

        eng = create_engine(url, pool_pre_ping=True, pool_recycle=1800, future=True)
        _init_schema(eng)
        _engine = eng
        logger.info("Result cache enabled (%s)", eng.url.render_as_string(hide_password=True))
    except Exception as exc:  # pragma: no cover - depends on external DB
        logger.warning("Result cache unavailable (%s) — running without cache", exc)
        _engine = None
    return _engine


def reset() -> None:
    """Test hook: drop the memoised engine so the next call rebuilds from env."""
    global _engine, _engine_ready, _json_columns, _ts_column, _date_columns
    global _pg_dialect
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _engine_ready = False
    _json_columns = False
    _ts_column = False
    _date_columns = False
    _pg_dialect = False


# ── JSON column helpers ───────────────────────────────────────────────────────

def _json_load(value):
    """Decode a stored payload/extras value.

    A JSONB column comes back from psycopg2 already parsed into Python objects,
    while a TEXT column (SQLite, or a pre-migration table) comes back as a
    string — accept either so both schemas read identically.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value


def _json_param(name: str) -> str:
    """Bind-parameter expression for a JSON column."""
    return f"CAST(:{name} AS JSONB)" if _json_columns else f":{name}"


def _ts_param(name: str) -> str:
    """Bind-parameter expression for the created_at column."""
    return f"CAST(:{name} AS TIMESTAMPTZ)" if _ts_column else f":{name}"


def _date_param(name: str) -> str:
    """Bind-parameter expression for a period_* column."""
    return f"CAST(:{name} AS DATE)" if _date_columns else f":{name}"


# ── Freshness ─────────────────────────────────────────────────────────────────

def _is_stale(created_at_iso: str) -> bool:
    """True if a current-month entry is older than the refresh window."""
    try:
        created = datetime.fromisoformat(str(created_at_iso))
    except Exception:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - created
    return age.total_seconds() > CURRENT_PERIOD_TTL_DAYS * 86400


# ── Public API ────────────────────────────────────────────────────────────────

def cache_get(analysis: str, date_key: str, bbox, is_current: bool):
    """Return the stored response dict for this request, or None on a miss.

    ``is_current`` marks a request whose date(s) include the running month; such
    a hit is discarded once it passes the TTL so newer scenes get a fresh run.
    Any error is swallowed (returns None) so a flaky cache never blocks a request.
    """
    engine = _get_engine()
    if engine is None:
        return None

    key = make_key(analysis, date_key, bbox)
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT payload, created_at FROM {_TABLE} WHERE cache_key = :k"),
                {"k": key},
            ).fetchone()

        if row is None:
            return None
        payload_value, created_at = row[0], row[1]

        if is_current and _is_stale(created_at):
            logger.info("Cache STALE (current period): %s", key)
            return None

        data = _json_load(payload_value)
        data["cached"] = True
        # Postgres now hands back a datetime (TIMESTAMPTZ); SQLite still returns
        # the ISO string it stored. Normalise so the client sees one format.
        data["cachedAt"] = (
            created_at.isoformat() if isinstance(created_at, datetime) else str(created_at)
        )
        logger.info("Cache HIT: %s", key)
        return data
    except Exception as exc:  # pragma: no cover - depends on external DB
        logger.warning("Cache read failed (%s) — computing fresh", exc)
        return None


def _store_derived(conn, cache_key, payload, extras, bounds, lon, lat, observed_on):
    """Populate both derived tables for a row that was just written.

    Runs inside the caller's transaction, on the connection that inserted the
    parent. A payload with no ``classes`` array (change detection, an error
    response, an older cached shape) simply writes nothing.

    The parent id is re-read by ``cache_key`` rather than captured from the
    INSERT: ``RETURNING`` is Postgres-only and ``lastrowid`` SQLite-only, and a
    cache write happens once per expensive analysis, so the extra round trip is
    free next to the Sentinel Hub fetch it follows.
    """
    from sqlalchemy import text

    rows = payload.get("classes") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return 0, 0

    cache_id = conn.execute(
        text(f"SELECT id FROM {_TABLE} WHERE cache_key = :k"), {"k": cache_key},
    ).scalar()
    if cache_id is None:  # pragma: no cover - parent was just inserted
        return 0, 0

    summaries = _insert_classes(conn, cache_id, rows, lon, lat)
    points = _store_points(conn, cache_id, extras, bounds, observed_on)
    return summaries, points


def _resolve_class_ids(conn, wanted: dict) -> dict:
    """Map class names to ``land_cover_class`` ids, creating rows as needed.

    ``wanted`` is ``{name: colour or None}``. Resolved in one pass per write
    rather than per row: there are only ever four classes, but a single classify
    contributes tens of thousands of point rows, and looking the id up each time
    would dominate the write.
    """
    from sqlalchemy import text

    out = {}
    for name, color in wanted.items():
        name = str(name)[:32]
        if not name:
            continue
        color = str(color)[:7] if color else None
        row = conn.execute(
            text(f"SELECT id, color FROM {_LOOKUP_TABLE} WHERE name = :n"), {"n": name},
        ).fetchone()
        if row is None:
            conn.execute(
                text(f"INSERT INTO {_LOOKUP_TABLE} (name, color) VALUES (:n, :c)"),
                {"n": name, "c": color},
            )
            row = conn.execute(
                text(f"SELECT id, color FROM {_LOOKUP_TABLE} WHERE name = :n"), {"n": name},
            ).fetchone()
        elif color and not row[1]:
            # Learned the colour from a later payload than the one that created it.
            conn.execute(
                text(f"UPDATE {_LOOKUP_TABLE} SET color = :c WHERE id = :i"),
                {"c": color, "i": row[0]},
            )
        if row is not None:
            out[name] = row[0]
    return out


def _insert_classes(conn, cache_id, rows, lon: float, lat: float) -> int:
    """Write one summary row per well-formed class entry. Returns how many landed.

    ``lon``/``lat`` are accepted but not stored — the analysed centre lives on
    the parent row. They remain in the signature because the backfill path reads
    them from the parent anyway and callers are clearer for passing them.

    Entries are skipped rather than rejected wholesale: a payload is data we
    already returned to a client, so a surprising value should cost at most one
    missing summary row, never the write.
    """
    from sqlalchemy import text

    entries = [e for e in rows if isinstance(e, dict) and e.get("name")]
    if not entries:
        return 0

    ids = _resolve_class_ids(conn, {e["name"]: e.get("color") for e in entries})

    written = 0
    for entry in entries:
        class_id = ids.get(str(entry["name"])[:32])
        if class_id is None:
            continue
        try:
            pixels = int(entry.get("pixels") or 0)
            percent = float(entry.get("percent") or 0.0)
            area = float(entry.get("areaKm2") or 0.0)
        except (TypeError, ValueError):
            continue
        conn.execute(
            text(
                f"INSERT INTO {_CLASS_TABLE} "
                f"(cache_id, class_id, pixel_count, percent, area_km2) "
                f"VALUES (:cid, :kid, :px, :pct, :area)"
            ),
            {"cid": cache_id, "kid": class_id, "px": pixels, "pct": percent, "area": area},
        )
        written += 1
    return written


def _store_points(conn, cache_id, extras, bounds, observed_on) -> int:
    """Upsert the per-location classification map for one analysed area.

    This is the "latest observation wins" rule: a location already carrying a
    newer ``observed_on`` is left untouched, so replaying an older month cannot
    undo a newer one, whatever order the searches arrive in. Expressed as a
    conditional ON CONFLICT so ordering is enforced by the database rather than
    by hoping callers write in sequence.

    Returns the number of rows offered, not the number actually changed — the
    database decides the latter and reporting it would need another round trip.
    """
    from sqlalchemy import text

    if observed_on is None or cache_id is None:
        return 0

    # Collapse the grid onto the snapped point grid first. Neighbouring cells
    # can round to the same location; last one wins, which is arbitrary but
    # consistent, and it keeps the executemany batch free of self-conflicts
    # (Postgres rejects a batch that hits the same key twice).
    collapsed = {}
    labels = {}
    for lon, lat, label in _grid_points(extras, bounds):
        collapsed[snap_point(lon, lat)] = label
        labels[label] = None
    if not collapsed:
        return 0

    ids = _resolve_class_ids(conn, labels)
    now = datetime.now(timezone.utc).isoformat()
    observed = observed_on.isoformat()

    params = [
        {"lon": lon, "lat": lat, "kid": ids[label],
         "obs": observed, "cid": cache_id, "ts": now}
        for (lon, lat), label in collapsed.items()
        if label in ids
    ]
    if not params:
        return 0

    # Written as chunked multi-row INSERTs rather than one parameterised
    # statement executed many times. psycopg2 implements executemany as a loop
    # of individual round trips, which for a 20,000-cell grid measured at 67
    # seconds against a local Postgres -- time that would be added to every
    # classify response. Batching turns it into a few dozen statements.
    obs_expr = "CAST(:obs AS DATE)" if _pg_dialect else ":obs"
    ts_expr = "CAST(:ts AS TIMESTAMPTZ)" if _pg_dialect else ":ts"
    tail = (
        f"ON CONFLICT (longitude, latitude) DO UPDATE SET "
        f"  class_id    = EXCLUDED.class_id, "
        f"  observed_on = EXCLUDED.observed_on, "
        f"  cache_id    = EXCLUDED.cache_id, "
        f"  updated_at  = EXCLUDED.updated_at "
        f"WHERE {_POINT_TABLE}.observed_on < EXCLUDED.observed_on"
    )

    size = _POINT_CHUNK_PG if _pg_dialect else _POINT_CHUNK_SQLITE
    for start in range(0, len(params), size):
        chunk = params[start:start + size]
        # observed_on / cache_id / updated_at are identical for every row in a
        # write, so they are bound once and referenced from each tuple.
        bound = {"obs": observed, "cid": cache_id, "ts": now}
        tuples = []
        for i, row in enumerate(chunk):
            bound[f"x{i}"] = row["lon"]
            bound[f"y{i}"] = row["lat"]
            bound[f"k{i}"] = row["kid"]
            tuples.append(f"(:x{i}, :y{i}, :k{i}, {obs_expr}, :cid, {ts_expr})")
        conn.execute(
            text(
                f"INSERT INTO {_POINT_TABLE} "
                f"(longitude, latitude, class_id, observed_on, cache_id, updated_at) "
                f"VALUES {', '.join(tuples)} {tail}"
            ),
            bound,
        )
    return len(params)


def cache_put(
    analysis: str,
    date_key: str,
    bbox,
    is_current: bool,
    payload: dict,
    extras: dict | None = None,
) -> None:
    """Store (or overwrite) the response for this request. Best-effort.

    The bbox columns hold the *actual* (unquantised) bounds so a later smaller
    request can be tested for containment precisely; only the cache_key uses the
    quantised bbox. ``longitude``/``latitude`` are the centre of that box.
    ``extras`` is optional per-analysis data needed to serve a sub-area from this
    entry (e.g. the land-cover label grid) — never sent to the client, only used
    to crop.
    """
    engine = _get_engine()
    if engine is None:
        return

    key = make_key(analysis, date_key, bbox)
    w, s, e, n = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    lon, lat = (w + e) / 2.0, (s + n) / 2.0
    period_start, period_end = _period_dates(date_key)
    try:
        body = json.dumps(payload, separators=(",", ":"))
        extras_body = json.dumps(extras, separators=(",", ":")) if extras is not None else None
    except (TypeError, ValueError) as exc:
        logger.warning("Cache put skipped — payload not JSON-serialisable (%s)", exc)
        return

    try:
        from sqlalchemy import text

        # Portable upsert: delete-then-insert in one transaction avoids the
        # dialect gap between Postgres ON CONFLICT and SQLite's UPSERT.
        with engine.begin() as conn:
            # Child rows go first and explicitly: ON DELETE CASCADE handles this
            # on Postgres, but SQLite only enforces foreign keys when the
            # per-connection `PRAGMA foreign_keys` is on, which it is not by
            # default. Deleting here is correct on both.
            #
            # classification_point is deliberately NOT deleted. It is a map of
            # ground truth, not a child of this response: its rows outlive the
            # cached payload that produced them, which is why its foreign key is
            # ON DELETE SET NULL.
            conn.execute(
                text(
                    f"DELETE FROM {_CLASS_TABLE} WHERE cache_id IN "
                    f"(SELECT id FROM {_TABLE} WHERE cache_key = :k)"
                ),
                {"k": key},
            )
            conn.execute(text(f"DELETE FROM {_TABLE} WHERE cache_key = :k"), {"k": key})
            conn.execute(
                text(
                    f"INSERT INTO {_TABLE} "
                    f"(cache_key, analysis, date_key, period_start, period_end, "
                    f" west, south, east, north, "
                    f" longitude, latitude, is_current, payload, extras, created_at) "
                    f"VALUES (:k, :a, :d, {_date_param('ps')}, {_date_param('pe')}, "
                    f":w, :s, :e, :n, :lon, :lat, :cur, "
                    f"{_json_param('p')}, {_json_param('x')}, {_ts_param('ts')})"
                ),
                {
                    "k": key, "a": analysis, "d": date_key,
                    "ps": period_start.isoformat() if period_start else None,
                    "pe": period_end.isoformat() if period_end else None,
                    "w": w, "s": s, "e": e, "n": n,
                    "lon": lon, "lat": lat,
                    "cur": bool(is_current),
                    "p": body, "x": extras_body,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
            summaries, points = _store_derived(
                conn, key, payload, extras, (w, s, e, n), lon, lat, period_start,
            )
        logger.info(
            "Cache STORE: %s (%d bytes, %d class rows, %d points)",
            key, len(body), summaries, points,
        )
    except Exception as exc:  # pragma: no cover - depends on external DB
        logger.warning("Cache write failed (%s) — response still returned", exc)


def cache_get_containing(analysis: str, date_key: str, req_bbox, is_current: bool):
    """Find the tightest cached entry whose bounds fully enclose ``req_bbox``.

    Enables serving a smaller area that sits entirely inside a previously
    computed one with zero Sentinel Hub calls (the caller crops the result).
    Returns ``{"payload", "extras", "bounds"}`` or None. Same analysis + date
    only; current-period entries past the TTL are skipped.
    """
    engine = _get_engine()
    if engine is None:
        return None

    rw, rs, re_, rn = (float(v) for v in req_bbox)
    eps = 1e-9  # float-safety, not fuzzy matching
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT payload, extras, west, south, east, north, created_at "
                    f"FROM {_TABLE} "
                    f"WHERE analysis = :a AND date_key = :d "
                    f"AND west <= :rw + :eps AND south <= :rs + :eps "
                    f"AND east >= :re - :eps AND north >= :rn - :eps "
                    f"ORDER BY (east - west) * (north - south) ASC "
                    f"LIMIT 1"
                ),
                {"a": analysis, "d": date_key, "rw": rw, "rs": rs, "re": re_, "rn": rn, "eps": eps},
            ).fetchone()

        if row is None:
            return None
        payload_value, extras_value, w, s, e, n, created_at = row

        if is_current and _is_stale(created_at):
            logger.info("Containing entry STALE (current period) for %s/%s", analysis, date_key)
            return None

        logger.info("Cache CONTAINING hit for %s/%s", analysis, date_key)
        return {
            "payload": _json_load(payload_value),
            "extras": _json_load(extras_value) if extras_value else None,
            "bounds": (float(w), float(s), float(e), float(n)),
        }
    except Exception as exc:  # pragma: no cover - depends on external DB
        logger.warning("Cache containment read failed (%s) — computing fresh", exc)
        return None
