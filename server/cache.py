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
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

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
    """DDL for a fresh table.

    ``id`` is the primary key — an opaque surrogate that stays valid even if the
    natural key ever has to change; ``cache_key`` keeps its uniqueness so the
    lookups below are unaffected. ``longitude``/``latitude`` are the centre of
    the analysed box, denormalised so a row can be located on a map (or matched
    against a user's position) without re-deriving it from the four bounds.
    created_at is stored as an ISO-8601 UTC string so we never depend on a
    dialect's datetime binding (SQLite in particular). DOUBLE PRECISION carries
    the real bbox for the "sub-area fully inside a computed one" lookup.
    """
    id_col = "id BIGSERIAL PRIMARY KEY" if pg else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    json_type = "JSONB" if pg else "TEXT"
    return f"""
    CREATE TABLE IF NOT EXISTS {_TABLE} (
        {id_col},
        cache_key   TEXT NOT NULL UNIQUE,
        analysis    TEXT NOT NULL,
        date_key    TEXT NOT NULL,
        west        DOUBLE PRECISION NOT NULL,
        south       DOUBLE PRECISION NOT NULL,
        east        DOUBLE PRECISION NOT NULL,
        north       DOUBLE PRECISION NOT NULL,
        longitude   DOUBLE PRECISION,
        latitude    DOUBLE PRECISION,
        is_current  INTEGER NOT NULL DEFAULT 0,
        payload     {json_type} NOT NULL,
        extras      {json_type},
        created_at  TEXT NOT NULL
    )
    """


def _index_sql() -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_lookup "
        f"ON {_TABLE} (analysis, date_key)"
    )


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
    if pk_cols == ["id"] and "longitude" in cols and "latitude" in cols:
        return

    carried = [c for c in _CARRIED_COLUMNS if c in cols]
    lon = "longitude" if "longitude" in cols else "(west + east) / 2.0"
    lat = "latitude" if "latitude" in cols else "(south + north) / 2.0"
    old = f"{_TABLE}_old"

    with engine.begin() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {old}"))
        conn.execute(text(f"ALTER TABLE {_TABLE} RENAME TO {old}"))
        conn.execute(text(_create_table_sql(pg=False)))
        conn.execute(text(
            f"INSERT INTO {_TABLE} ({', '.join(carried)}, longitude, latitude) "
            f"SELECT {', '.join(carried)}, {lon}, {lat} FROM {old}"
        ))
        # Drops the old table's indexes too, freeing the shared index name.
        conn.execute(text(f"DROP TABLE {old}"))
    logger.info("Result cache schema rebuilt (SQLite)")


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

    with engine.begin() as conn:
        conn.execute(text(_index_sql()))

    # Whether writes need an explicit JSONB cast is read back from the database
    # rather than assumed, so a migration that could not run still leaves a
    # working (text-column) cache instead of failing every insert.
    payload_type = next(
        (str(c["type"]).upper() for c in inspect(engine).get_columns(_TABLE)
         if c["name"] == "payload"),
        "",
    )
    _json_columns = "JSON" in payload_type


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
    global _engine, _engine_ready, _json_columns
    if _engine is not None:
        try:
            _engine.dispose()
        except Exception:
            pass
    _engine = None
    _engine_ready = False
    _json_columns = False


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
        data["cachedAt"] = str(created_at)
        logger.info("Cache HIT: %s", key)
        return data
    except Exception as exc:  # pragma: no cover - depends on external DB
        logger.warning("Cache read failed (%s) — computing fresh", exc)
        return None


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
            conn.execute(text(f"DELETE FROM {_TABLE} WHERE cache_key = :k"), {"k": key})
            conn.execute(
                text(
                    f"INSERT INTO {_TABLE} "
                    f"(cache_key, analysis, date_key, west, south, east, north, "
                    f" longitude, latitude, is_current, payload, extras, created_at) "
                    f"VALUES (:k, :a, :d, :w, :s, :e, :n, :lon, :lat, :cur, "
                    f"{_json_param('p')}, {_json_param('x')}, :ts)"
                ),
                {
                    "k": key, "a": analysis, "d": date_key,
                    "w": w, "s": s, "e": e, "n": n,
                    "lon": lon, "lat": lat,
                    "cur": 1 if is_current else 0,
                    "p": body, "x": extras_body,
                    "ts": datetime.now(timezone.utc).isoformat(),
                },
            )
        logger.info("Cache STORE: %s (%d bytes)", key, len(body))
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
