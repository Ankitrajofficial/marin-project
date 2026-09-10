"""Load harbours from OSM. Recall needs destinations.

    python -m jobs.ingest_harbours
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.config import settings
from app.db import close_pool, get_conn, open_pool
from app.zones.osm_harbours import fetch_all, write_harbours


async def run() -> int:
    await open_pool()
    try:
        rows = await fetch_all()
        async with get_conn() as conn:
            n = await write_harbours(conn, rows)
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT harbour_type, depth_source, count(*)
                      FROM harbours GROUP BY 1,2 ORDER BY 3 DESC""")
                breakdown = await cur.fetchall()
                await cur.execute(
                    "SELECT count(*) FILTER (WHERE depth_m IS NOT NULL), count(*) "
                    "FROM harbours")
                with_depth, total = await cur.fetchone()

        print(f"\nwrote {n} harbours\n")
        print(f"  {'type':<12}{'depth_source':<15}{'count':>6}")
        print(f"  {'-'*12}{'-'*15}{'-'*6}")
        for t, ds, c in breakdown:
            print(f"  {t or '-':<12}{ds:<15}{c:>6}")
        print(f"\n  {with_depth} of {total} have a usable depth. Draft "
              f"compatibility is UNKNOWN for the rest -- core/recall.py flags "
              f"those rather than assuming a vessel fits.")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    sys.exit(asyncio.run(run()))
