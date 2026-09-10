"""Apply db/*.sql to whatever database DATABASE_URL points at.

Local development gets its schema from docker-compose, which mounts db/ into
the image's docker-entrypoint-initdb.d. That mechanism has two properties that
make it useless for a hosted database: it runs only against an EMPTY data
directory, and it requires bind-mounting repo files into the Postgres
container. A managed Postgres (Supabase, Neon) offers neither -- the database
already exists and there is nothing to mount.

So the API applies its own schema at boot instead. That is safe here, and
would not be in general, because every statement in db/ is idempotent:
CREATE ... IF NOT EXISTS throughout, create_hypertable(if_not_exists => TRUE),
and ON CONFLICT DO NOTHING on the source seed. Re-running is a no-op, so this
can sit in front of uvicorn on every cold start without a migration tool or a
version table.

Deliberately NOT a migration system. It creates the schema that db/ describes;
it cannot alter a table that already exists with a different shape. The day a
column changes, this stops being enough and a real migration tool starts.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psycopg

log = logging.getLogger("init_db")

#: Repo root is two levels up from backend/jobs/. Overridable because the
#: Docker image may lay the tree out differently than a git checkout.
SQL_DIR = Path(os.getenv("ORCA_DB_SQL_DIR") or Path(__file__).resolve().parents[2] / "db")


def _on_notice(diag: psycopg.errors.Diagnostic) -> None:
    """Surface server NOTICEs in our own log.

    Not decoration: the Timescale branch in 01_extensions.sql reports which
    path it took only through a NOTICE. Swallowing those would hide whether
    the deployed database is partitioned or plain -- exactly the fact worth
    knowing when the hosted and local schemas differ.
    """
    if msg := (diag.message_primary or "").strip():
        log.info("postgres: %s", msg)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        log.error("DATABASE_URL is not set")
        return 1

    scripts = sorted(SQL_DIR.glob("*.sql"))
    if not scripts:
        log.error("no .sql files under %s", SQL_DIR)
        return 1

    # Filename order is load-bearing: 01 extensions, 02 schema, 03 seed. The
    # schema cannot create a geometry column before postgis exists.
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.add_notice_handler(_on_notice)
        for path in scripts:
            log.info("applying %s", path.name)
            conn.execute(path.read_text())

    log.info("schema ready (%d script(s) applied)", len(scripts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
