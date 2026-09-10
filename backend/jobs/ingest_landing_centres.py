"""Load INCOIS fishing landing centres as recall destinations.

    python -m jobs.ingest_landing_centres

Additive: OSM harbours are kept. harbours.source_id records which source each
destination came from.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.config import settings
from app.db import close_pool, get_conn, open_pool
from app.zones.incois_landing_centres import SNAPSHOT_DATE, fetch_all
from app.zones.osm_harbours import write_harbours


async def run() -> int:
    await open_pool()
    try:
        rows = await fetch_all()
        async with get_conn() as conn:
            n = await write_harbours(conn, rows)
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT source_id, harbour_type, count(*),
                           count(*) FILTER (WHERE depth_m IS NOT NULL)
                      FROM harbours GROUP BY 1,2 ORDER BY 3 DESC""")
                breakdown = await cur.fetchall()
                await cur.execute("SELECT count(*) FROM harbours")
                total = (await cur.fetchone())[0]

        print(f"\nwrote {n} INCOIS landing centres\n")
        print(f"  {'source':<16}{'type':<18}{'count':>6}{'with depth':>12}")
        print(f"  {'-'*16}{'-'*18}{'-'*6}{'-'*12}")
        for src, typ, cnt, wd in breakdown:
            print(f"  {src or '-':<16}{typ or '-':<18}{cnt:>6}{wd:>12}")
        print(f"\n  {total} destinations total. Both sources kept; source_id on")
        print(f"  each row lets a recall trace say where a destination came from.")
        print(f"  INCOIS layer is a SNAPSHOT frozen at {SNAPSHOT_DATE} -- locations")
        print(f"  only. Surfaced as the data age, never presented as live.")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    sys.exit(asyncio.run(run()))
