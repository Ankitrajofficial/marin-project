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
from app.adapters import imd_cap
from app.zones import marine_regions, osm_protected
from app.zones.base import write_zones

log = logging.getLogger("ingest_zones")


async def run(skip_osm: bool, skip_imd: bool = False) -> int:
    await open_pool()
    try:
        zones = await marine_regions.fetch_all()
        if not skip_osm:
            zones += await osm_protected.fetch_all()
        if not skip_imd:
            # The only live Indian government source in ORCA. Failure here must
            # not silently drop official warnings, so it is reported loudly and
            # the count is printed below either way.
            try:
                zones += await imd_cap.fetch_all()
            except Exception as e:
                log.error("IMD CAP ingest FAILED (%s: %s) -- official warnings "
                          "are NOT in this run", type(e).__name__, e)

        async with get_conn() as conn:
            n = await write_zones(conn, zones)
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT zone_type, authority, count(*),
                           count(*) FILTER (WHERE valid_until IS NOT NULL
                                              AND valid_until > now()) AS live
                      FROM hazard_zones GROUP BY 1,2 ORDER BY 1
                """)
                rows = await cur.fetchall()

        print(f"\nwrote {n} zones\n")
        print(f"  {'zone_type':<18}{'authority':<22}{'count':>6}{'in force':>10}")
        print(f"  {'-'*18}{'-'*22}{'-'*6}{'-'*10}")
        for zt, auth, cnt, live in rows:
            print(f"  {zt:<18}{auth:<22}{cnt:>6}{(live or 0):>10}")
        print("\n  Boundaries and protected areas are ADVISORY open data -- not")
        print("  Survey of India, no legal authority.")
        print("  IMD warnings are OFFICIAL (authority='official'), the only such")
        print("  rows in the database. Attribution to IMD is mandatory and is")
        print("  carried through the API to the UI.")
        return 0
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--skip-osm", action="store_true", help="skip OSM protected areas")
    p.add_argument("--skip-imd", action="store_true", help="skip IMD CAP warnings")
    args = p.parse_args(argv)
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    return asyncio.run(run(args.skip_osm, args.skip_imd))


if __name__ == "__main__":
    sys.exit(main())
