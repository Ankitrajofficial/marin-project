"""Load geofencing zones: maritime boundaries and marine protected areas.

    python -m jobs.ingest_zones

Idempotent -- re-running refreshes geometry in place.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.config import settings
from app.db import close_pool, get_conn, open_pool
from app.zones import marine_regions, osm_protected
from app.zones.base import write_zones

log = logging.getLogger("ingest_zones")


async def run(skip_osm: bool) -> int:
    await open_pool()
    try:
        zones = await marine_regions.fetch_all()
        if not skip_osm:
            zones += await osm_protected.fetch_all()

        async with get_conn() as conn:
            n = await write_zones(conn, zones)
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT zone_type, authority, count(*),
                           sum(CASE WHEN GeometryType(geom) LIKE '%%LINE%%'
                                    THEN 1 ELSE 0 END) AS lines
                      FROM hazard_zones GROUP BY 1,2 ORDER BY 1
                """)
                rows = await cur.fetchall()

        print(f"\nwrote {n} zones\n")
        print(f"  {'zone_type':<18}{'authority':<22}{'count':>6}{'lines':>7}")
        print(f"  {'-'*18}{'-'*22}{'-'*6}{'-'*7}")
        for zt, auth, cnt, lines in rows:
            print(f"  {zt:<18}{auth:<22}{cnt:>6}{lines:>7}")
        print("\n  All zones are ADVISORY open data. Not Survey of India, no legal authority.")
        return 0
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-osm", action="store_true", help="boundaries only")
    args = p.parse_args(argv)
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    return asyncio.run(run(args.skip_osm))


if __name__ == "__main__":
    sys.exit(main())
