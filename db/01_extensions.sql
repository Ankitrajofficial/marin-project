-- ORCA step 1: extensions.

-- postgis     REQUIRED. Every spatial predicate in core/ -- geofence
--             distances, routing geometry, recall harbour lookup -- is an
--             ST_* call, and several columns are geometry typed. Without it
--             the schema does not even create, which is the correct failure.
CREATE EXTENSION IF NOT EXISTS postgis;

-- vector      pgvector, for advisory_chunks.embedding (RAG over bulletins).
--             OPTIONAL, for the same reason timescaledb is: NOTHING in app/ or
--             jobs/ reads advisory_chunks or embedding today -- it is scaffolding
--             for retrieval over scraped bulletins, and grep finds no consumer.
--             A hard requirement here would let an extension no code uses take
--             down the schema, and with it the whole deploy, on any host that
--             does not ship pgvector. 02_schema.sql skips the one table that
--             needs it and creates everything else.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
    RAISE NOTICE 'pgvector enabled -- advisory_chunks.embedding available';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector unavailable (%) -- advisory_chunks will be skipped', SQLERRM;
END $$;

-- timescaledb OPTIONAL, and deliberately so.
--
-- It buys chunk partitioning and retention on the three time-series tables.
-- That is a performance and housekeeping win, NOT a correctness one: no query
-- in app/ or jobs/ calls a Timescale function -- no time_bucket, no
-- continuous aggregate. Drop the extension and every query returns the same
-- rows, more slowly, at a volume where "more slowly" is not yet measurable.
--
-- It matters because managed Postgres (Supabase, Neon, RDS) does not ship
-- Timescale. A bare CREATE EXTENSION here would make the schema undeployable
-- on every free host, trading a deployable demo for an optimisation the data
-- volume does not need. Local docker runs the timescaledb-ha image and takes
-- the fast path; hosted Postgres takes the plain one and says so in the log.
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS timescaledb;
    RAISE NOTICE 'timescaledb enabled -- time-series tables will be partitioned';
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'timescaledb unavailable (%) -- continuing with plain tables', SQLERRM;
END $$;
