-- ORCA step 1: schema. 9 tables.
--
-- Conventions that the rest of the codebase depends on:
--   * h3_cell is the H3 index as its 15-char hex STRING. This image has no
--     h3-pg extension, so cells are computed in Python (h3 lib) at the
--     adapter boundary. Resolution is NOT baked in here -- core/grid.py owns
--     that choice, so changing resolution never means a migration.
--   * issued_time is only interpretable together with issued_time_kind.
--     Never compare data-age across sources without checking it.
--   * Every value in observations uses the ONE canonical unit for its
--     variable -- SI-derived, defined in adapters/base.py CANONICAL_UNITS
--     (sst is degC and chl is mg/m3 by convention, not K and kg/m3).
--     Unit conversion is an adapter's job, never core/'s.
--   * geom is SRID 4326 (WGS84 lon/lat) everywhere.

-- ---------------------------------------------------------------- sources
-- One row per upstream data source. Static, hand-seeded, small.
CREATE TABLE IF NOT EXISTS sources (
    source_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    access_mode TEXT NOT NULL,          -- 'api' | 'scrape'
    reliability REAL NOT NULL,          -- see COMMENT below
    base_url    TEXT,
    notes       TEXT
);

COMMENT ON COLUMN sources.reliability IS
  'Per-source prior in [0,1]. core/fusion.py uses this to arbitrate when two '
  'sources disagree about the same (h3_cell, variable, time bin). It is a '
  'weight on belief, not an accuracy claim -- a scraped official bulletin '
  'outranks a modelled API value precisely because it is authoritative.';

-- ------------------------------------------------------------- harbours
-- Candidate safe-harbour set for core/recall.py and core/routing.py.
CREATE TABLE IF NOT EXISTS harbours (
    harbour_id TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    depth_m    DOUBLE PRECISION,        -- usable depth. Limits which vessels
                                        -- can enter (see vessels.draft_m).
    -- HARD FLAG. OpenStreetMap carries NO depth or draught tag on ANY harbour
    -- in our AOI -- verified, 103 features, zero tagged. So depth_m is NULL
    -- almost everywhere, and 'unknown' is the normal case rather than an edge
    -- case. NOT NULL forces the loader to say which it is, and core/recall.py
    -- must treat 'unknown' as a candidate WITH REDUCED CONFIDENCE, never as
    -- "deep enough".
    --
    -- Deliberately NOT filled from GEBCO. GEBCO is seabed bathymetry on a
    -- ~450 m grid; usable harbour depth is a dredged, maintained channel
    -- figure that GEBCO cannot see. Substituting one for the other would
    -- manufacture a safety claim out of data that does not contain one.
    depth_source TEXT NOT NULL DEFAULT 'unknown',
    harbour_type TEXT,                  -- fishing | marina | port | shipyard | unknown
    capacity   INTEGER,
    source_id  TEXT REFERENCES sources(source_id),
    source_url TEXT,
    meta       JSONB,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT harbours_depth_source CHECK (
        depth_source IN ('osm', 'chart', 'manual', 'unknown')),
    -- Generated, so geom can never drift out of sync with lat/lon.
    geom       GEOMETRY(Point, 4326)
               GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX IF NOT EXISTS harbours_geom_idx ON harbours USING GIST (geom);

-- -------------------------------------------------------------- vessels
-- Vessel registry. Static attributes only; positions live in the hypertable.
CREATE TABLE IF NOT EXISTS vessels (
    mmsi         TEXT PRIMARY KEY,
    name         TEXT,
    vessel_class TEXT,
    cruise_speed DOUBLE PRECISION,      -- m/s (SI) -- feeds time-to-harbour
    draft_m      DOUBLE PRECISION,
    home_harbour TEXT                   -- harbours.harbour_id, intentionally
                                        -- unconstrained: see note in 03 seed
);

-- ----------------------------------------------------- vessel_positions
-- AIS track points. High volume, 30d retention.
CREATE TABLE IF NOT EXISTS vessel_positions (
    ts       TIMESTAMPTZ NOT NULL,
    mmsi     TEXT NOT NULL,             -- deliberately NO FK to vessels: AIS
                                        -- reports craft we have never seen,
                                        -- and a FK would drop those rows.
    lat      DOUBLE PRECISION,
    lon      DOUBLE PRECISION,
    sog      DOUBLE PRECISION,          -- speed over ground, m/s (SI)
    cog      DOUBLE PRECISION,          -- course over ground, degrees true
    h3_cell  TEXT,
    geom     GEOMETRY(Point, 4326)
             GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED,
    -- Idempotent ingest: one position per vessel per instant.
    CONSTRAINT vessel_positions_uniq UNIQUE (mmsi, ts)
);

-- --------------------------------------------------------- hazard_zones
-- Static/slow polygons: restricted areas, reefs, declared cyclone zones.
CREATE TABLE IF NOT EXISTS hazard_zones (
    zone_id     TEXT PRIMARY KEY,
    zone_type   TEXT NOT NULL,    -- 'imbl' | 'eez' | 'territorial_sea'
                                  -- 'contiguous_zone' | 'baseline' | 'mpa'
    name        TEXT,

    -- Geometry(Geometry), not Geometry(Polygon): a maritime boundary is a
    -- LINE, not an area. The India-Sri Lanka IMBL is a treaty line; the EEZ
    -- and an MPA are polygons. Forcing lines into a polygon column would mean
    -- either dropping the IMBL or fabricating an area for it -- and the IMBL
    -- is the single most consequential geometry in this table.
    geom        GEOMETRY(Geometry, 4326) NOT NULL,

    -- The thing you measure distance TO. For a polygon that is its boundary
    -- (distance to a polygon you are inside is 0, which tells a skipper
    -- nothing about how close they are to crossing out); for a line it is the
    -- line itself. Generated, so it can never disagree with geom.
    edge_geom   GEOMETRY(Geometry, 4326) GENERATED ALWAYS AS (
                    CASE WHEN GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON')
                         THEN ST_Boundary(geom) ELSE geom END
                ) STORED,

    -- ===================== PROVENANCE AND AUTHORITY =====================
    -- HARD GUARD, same pattern as risk_cells.simulated.
    --
    -- 'official'            an authoritative government definition
    -- 'open_data_advisory'  an open-data compilation. Everything currently in
    --                       this table is this. MarineRegions is a VLIZ
    --                       scientific compilation; OSM protected areas are
    --                       crowd-sourced. NEITHER is Survey of India, and
    --                       India regulates the depiction of its boundaries.
    --
    -- A distance-to-IMBL number reads as authoritative to a fisherman or an
    -- officer whether or not we meant it to. NOT NULL means the loader has to
    -- state which it is, and the API surfaces it on every response, so no
    -- endpoint can quietly present an advisory line as a legal one.
    authority   TEXT NOT NULL,
    attribution TEXT NOT NULL,    -- must be displayed wherever this is shown
    license     TEXT,
    source_id   TEXT REFERENCES sources(source_id),
    source_url  TEXT,
    meta        JSONB,            -- treaty date, IUCN class, upstream ids
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT hazard_zones_geom_type CHECK (
        GeometryType(geom) IN ('POLYGON', 'MULTIPOLYGON',
                               'LINESTRING', 'MULTILINESTRING')),
    CONSTRAINT hazard_zones_authority CHECK (
        authority IN ('official', 'open_data_advisory'))
);
CREATE INDEX IF NOT EXISTS hazard_zones_geom_idx ON hazard_zones USING GIST (geom);
CREATE INDEX IF NOT EXISTS hazard_zones_edge_idx ON hazard_zones USING GIST (edge_geom);
-- ST_DWithin/ST_Distance on geography are GEODESIC -- metres on the ellipsoid,
-- not degrees. That is the only correct way to answer "how many nautical miles
-- to the boundary". These indexes are what keep it fast.
CREATE INDEX IF NOT EXISTS hazard_zones_geog_idx
    ON hazard_zones USING GIST (CAST(geom AS geography));
CREATE INDEX IF NOT EXISTS hazard_zones_edge_geog_idx
    ON hazard_zones USING GIST (CAST(edge_geom AS geography));
CREATE INDEX IF NOT EXISTS hazard_zones_type_idx ON hazard_zones (zone_type);

-- --------------------------------------------------------- observations
-- The single normalized shape every adapter writes into. Zero computation
-- happens on the way in. This is the input side of core/fusion.py.
CREATE TABLE IF NOT EXISTS observations (
    valid_time  TIMESTAMPTZ NOT NULL,   -- when the observation/forecast APPLIES
    issued_time TIMESTAMPTZ,            -- when this value was ISSUED. What that
                                        -- means depends on issued_time_kind --
                                        -- read that before doing arithmetic
                                        -- with this column.
    issued_time_kind TEXT,              -- 'model_run'   real initialization time
                                        --               from the source
                                        -- 'fetch_proxy' wall-clock time WE
                                        --               retrieved it, because
                                        --               the source exposes no
                                        --               run time. An UPPER BOUND
                                        --               on data age, not the
                                        --               true age.
                                        -- core/fusion.py compares data-age
                                        -- ACROSS sources, so it must branch on
                                        -- this rather than assume both mean the
                                        -- same thing. Open-Meteo has no run time
                                        -- in its response (verified: no body
                                        -- field, no header); Copernicus and IMD
                                        -- do. Without this column that
                                        -- difference would be invisible at the
                                        -- point where it matters.
    h3_cell     TEXT NOT NULL,
    variable    TEXT NOT NULL,          -- e.g. 'wave_height', 'wind_speed', 'sst'
    value       DOUBLE PRECISION,
    unit        TEXT,                   -- canonical unit for this variable;
                                        -- stored for provenance. Enforced at
                                        -- ingest by the Observation contract.
    source_id   TEXT REFERENCES sources(source_id),
    confidence  REAL,
    geom        GEOMETRY(Point, 4326),  -- RAW sample location from the source,
                                        -- not the cell centroid. h3_cell already
                                        -- carries the cell, and this table has no
                                        -- lat/lon columns -- so storing the
                                        -- centroid here would discard the true
                                        -- position permanently.
    -- Idempotent ingest: re-running a fetch overwrites rather than duplicates.
    CONSTRAINT observations_uniq UNIQUE (source_id, h3_cell, variable, valid_time),
    CONSTRAINT observations_issued_kind_valid
        CHECK (issued_time_kind IN ('model_run', 'fetch_proxy')),
    -- A timestamp without its kind is unusable (you cannot tell a real issue
    -- time from a fetch proxy), and a kind without a timestamp is meaningless.
    -- They travel together or not at all.
    CONSTRAINT observations_issued_paired
        CHECK ((issued_time IS NULL) = (issued_time_kind IS NULL))
);

-- ----------------------------------------------------------- risk_cells
-- Output of core/risk.py: exceedance probability per cell per time step.
CREATE TABLE IF NOT EXISTS risk_cells (
    valid_time  TIMESTAMPTZ NOT NULL,
    h3_cell     TEXT NOT NULL,
    hazard_prob REAL,                   -- P(threshold exceeded), NOT a score
    uncertainty REAL,                   -- spread of the forecast ensemble
    drivers     JSONB,                  -- which variables/sources drove it,
                                        -- so the trace can explain the number
    -- HARD GUARD. True if ANY observation feeding this cell came from a
    -- scenario source (see config.SCENARIO_SOURCE_IDS). A simulated number
    -- must never be presentable as a real forecast, so the flag is a column
    -- rather than a key inside drivers: NOT NULL means core/ has to decide it
    -- explicitly, and an API handler cannot forget to select it the way it can
    -- forget a nested JSON field.
    simulated   BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (valid_time, h3_cell)
);

-- ------------------------------------------------------ advisory_chunks
-- Chunked text of scraped bulletins (INCOIS PFZ, IMD cyclone) for retrieval.
CREATE TABLE IF NOT EXISTS advisory_chunks (
    chunk_id    BIGSERIAL PRIMARY KEY,
    source_id   TEXT REFERENCES sources(source_id),
    issued_time TIMESTAMPTZ,
    content     TEXT NOT NULL,
    embedding   VECTOR(1024)
    -- No ANN index yet: the operator class depends on cosine vs L2, which is
    -- decided when the first embeddings land. Exact search is fine until then.
);

-- ------------------------------------------------- scenario machinery
-- Scenario injection lives entirely outside core/. core/ cannot tell a
-- scenario row from a real one except by source_id -- which is the point, and
-- also the danger: a scenario row and a real row for the same cell, variable
-- and time would BOTH be read by risk.py and fused into a blend of a calm sea
-- and a cyclone. A half-simulated hazard field is a forecast of nothing.
--
-- So overlapping real rows are MOVED ASIDE here for the life of the scenario
-- and moved back when it is cleared. Not deleted (clearing must restore them),
-- and not filtered on read (that would mean editing core/, which must stay
-- untouched). The scenario is the only world inside its window because the
-- real rows are physically not in `observations` while it runs.
CREATE TABLE IF NOT EXISTS observations_masked (
    scenario_id      TEXT NOT NULL,
    masked_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_time       TIMESTAMPTZ NOT NULL,
    issued_time      TIMESTAMPTZ,
    issued_time_kind TEXT,
    h3_cell          TEXT NOT NULL,
    variable         TEXT NOT NULL,
    value            DOUBLE PRECISION,
    unit             TEXT,
    source_id        TEXT,
    confidence       REAL,
    geom             GEOMETRY(Point, 4326)
);
CREATE INDEX IF NOT EXISTS observations_masked_scenario_idx
    ON observations_masked (scenario_id);

CREATE TABLE IF NOT EXISTS scenario_runs (
    scenario_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    activated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cleared_at   TIMESTAMPTZ,          -- NULL = currently active
    params       JSONB,                -- track, intensity, fleet seed
    counts       JSONB,                -- rows written / masked, for audit
    vessel_ids   TEXT[]                -- exact ids to delete on clear, so
                                       -- cleanup never guesses with LIKE
);
-- At most ONE active scenario, enforced by the database rather than by care.
-- Two overlapping scenarios would mask each other's rows and neither could be
-- cleanly restored.
CREATE UNIQUE INDEX IF NOT EXISTS scenario_runs_one_active
    ON scenario_runs ((cleared_at IS NULL)) WHERE cleared_at IS NULL;

-- ---------------------------------------------------------- ingest_gaps
-- Every period an ingest stream was NOT listening. A durable table, not a log
-- line, because the question it answers is asked after the fact: "we have no
-- position for that boat between 14:02 and 14:09 -- was it not transmitting,
-- or were we not listening?" Those are completely different answers for a
-- recall list, and a log lost to a restart cannot distinguish them.
CREATE TABLE IF NOT EXISTS ingest_gaps (
    gap_id     BIGSERIAL PRIMARY KEY,
    source_id  TEXT REFERENCES sources(source_id),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at   TIMESTAMPTZ,             -- NULL = still down
    reason     TEXT,
    messages_before BIGINT              -- received before the gap, for context
);
CREATE INDEX IF NOT EXISTS ingest_gaps_source_time_idx
    ON ingest_gaps (source_id, started_at DESC);

-- --------------------------------------------------------------- traces
-- The audit record enforcing the hard rule: every number an agent utters is
-- recorded here with the core/ function that produced it, its source id and
-- its data-age.
CREATE TABLE IF NOT EXISTS traces (
    trace_id     BIGSERIAL PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    session_id   TEXT,             -- groups a multi-turn conversation.
                                   -- Conversation STATE lives in LangGraph's
                                   -- in-memory checkpointer and is lost when
                                   -- the API restarts; this table is the
                                   -- durable record. Good enough for a demo,
                                   -- not for production -- see agents/graph.py.
    user_query   TEXT,
    steps        JSONB,             -- every node and tool call: validated
                                   -- input, full output, duration. Replayable,
                                   -- not a summary.
    final_answer TEXT,
    -- NULL means geofencing was not involved. That is NOT the same as
    -- advisory_only=false, which would claim an authoritative boundary.
    advisory_only BOOLEAN,
    -- Same guard as risk_cells.simulated: if any number in this trace came
    -- from scenario data, the whole trace is simulated and the API says so.
    simulated    BOOLEAN NOT NULL DEFAULT false
);

-- ===================================================== hypertables ======
-- Partition the three high-volume time-series tables. Timescale requires the
-- partitioning column to appear in every unique index, which is why the
-- constraints above all include valid_time / ts.
SELECT create_hypertable('observations',     'valid_time', if_not_exists => TRUE);
SELECT create_hypertable('vessel_positions', 'ts',         if_not_exists => TRUE);
SELECT create_hypertable('risk_cells',       'valid_time', if_not_exists => TRUE);

-- ========================================================= indexes ======
-- The hot read path: "latest value of variable V in cell C".
CREATE INDEX IF NOT EXISTS observations_cell_var_time_idx
    ON observations (h3_cell, variable, valid_time DESC);
CREATE INDEX IF NOT EXISTS observations_geom_idx
    ON observations USING GIST (geom);

CREATE INDEX IF NOT EXISTS vessel_positions_mmsi_time_idx
    ON vessel_positions (mmsi, ts DESC);
CREATE INDEX IF NOT EXISTS vessel_positions_geom_idx
    ON vessel_positions USING GIST (geom);

-- Partial index: the common query is "real forecasts only", and simulated
-- rows are rare, so indexing the exception keeps it small.
CREATE INDEX IF NOT EXISTS risk_cells_simulated_idx
    ON risk_cells (valid_time DESC) WHERE simulated;

-- ======================================================= retention ======
-- Drop raw rows older than 30 days. No rollup: continuous aggregates for
-- longer history are a later step, deliberately not part of step 1.
SELECT add_retention_policy('observations',     INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('vessel_positions', INTERVAL '30 days', if_not_exists => TRUE);
SELECT add_retention_policy('risk_cells',       INTERVAL '30 days', if_not_exists => TRUE);
