"""Long-lived AIS subscriber.

    python -m jobs.ingest_ais                # run until interrupted
    python -m jobs.ingest_ais --seconds 120  # bounded window, for verification

Reconnects with backoff and records every gap it leaves in ingest_gaps.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.adapters.aisstream import DEFAULT_BBOX, AISStreamIngest
from app.config import settings
from app.db import close_pool, get_conn, open_pool

log = logging.getLogger("ingest_ais")


async def run(seconds: float | None) -> int:
    await open_pool()
    ingest = AISStreamIngest(bbox=DEFAULT_BBOX)
    try:
        stats = await ingest.run(get_conn, duration_s=seconds)

        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    SELECT count(*), count(DISTINCT mmsi),
                           min(ts), max(ts)
                      FROM vessel_positions""")
                n_pos, n_ves, t0, t1 = await cur.fetchone()
                await cur.execute("""
                    SELECT count(*), count(*) FILTER (WHERE draft_m IS NOT NULL),
                           count(*) FILTER (WHERE vessel_class IS NOT NULL)
                      FROM vessels""")
                v_all, v_draft, v_class = await cur.fetchone()
                await cur.execute("""
                    SELECT count(*), coalesce(sum(
                        EXTRACT(epoch FROM coalesce(ended_at, now()) - started_at)), 0)
                      FROM ingest_gaps WHERE source_id = 'aisstream'""")
                n_gaps, gap_s = await cur.fetchone()
                await cur.execute("""
                    SELECT vessel_class, count(*) FROM vessels
                     GROUP BY 1 ORDER BY 2 DESC""")
                classes = await cur.fetchall()

        print(f"\n--- AIS session ---")
        print(f"  messages received : {stats.messages}")
        print(f"  position reports  : {stats.positions}")
        print(f"  static messages   : {stats.statics}   (draught arrives here only)")
        print(f"  unparsed          : {stats.unparsed}")
        print(f"  reconnects        : {stats.reconnects}")
        print(f"\n--- stored ---")
        print(f"  vessel_positions  : {n_pos} rows, {n_ves} distinct MMSI")
        if t0:
            print(f"  position span     : {t0:%H:%M:%S} -> {t1:%H:%M:%S} UTC")
        print(f"  vessels           : {v_all} known, {v_class} with a class, "
              f"{v_draft} with a draught")
        print(f"  ingest gaps       : {n_gaps} rows, {gap_s:.0f}s not listening")
        if classes:
            print(f"\n  by class: " + ", ".join(f"{c or 'unidentified'}={n}"
                                                for c, n in classes))
        if v_all and not v_draft:
            print("\n  NOTE: no vessel reported a draught. Harbour draft "
                  "compatibility is unverifiable for all of them; recall flags "
                  "this rather than assuming they fit.")
        return 0
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=None,
                   help="stop after N seconds (default: run forever)")
    args = p.parse_args(argv)
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    try:
        return asyncio.run(run(args.seconds))
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 0


if __name__ == "__main__":
    sys.exit(main())
