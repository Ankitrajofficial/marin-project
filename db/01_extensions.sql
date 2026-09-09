-- ORCA step 1: extensions.
-- postgis     spatial types + GIST indexing for geom columns
-- timescaledb hypertable partitioning + retention policies on time-series
-- vector      pgvector, for advisory_chunks.embedding (RAG over bulletins)
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
