"""Compute the risk field from observations already in the database.

    python -m jobs.compute_risk
    python -m jobs.compute_risk --hours 48

Reads observations, computes exceedance probabilities per cell per hour, and
upserts into risk_cells. Reports the resulting distribution read back from the
database rather than from memory.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from app.core.grid import (
    disk,
    fetch_observations,
    index_observations,
)
from app.core.risk import HAZARDS, compute_risk_field, write_risk_cells
from app.config import settings
from app.db import close_pool, get_conn, open_pool

log = logging.getLogger("compute_risk")


async def observed_cells_and_times(conn, t0: datetime, t1: datetime):
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT DISTINCT h3_cell FROM observations
                WHERE valid_time >= %s AND valid_time < %s ORDER BY 1""",
            (t0, t1),
        )
        cells = [r[0] for r in await cur.fetchall()]
        await cur.execute(
            """SELECT DISTINCT valid_time FROM observations
                WHERE valid_time >= %s AND valid_time < %s ORDER BY 1""",
            (t0, t1),
        )
        times = [r[0] for r in await cur.fetchall()]
    return cells, times


async def report(conn) -> None:
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT count(*),
                   count(hazard_prob),
                   count(*) FILTER (WHERE (drivers->>'partial_coverage')::bool),
                   count(*) FILTER (WHERE drivers->'variables'->'wave_height'->>'origin' = 'neighbour'
                                       OR drivers->'variables'->'wind_speed'->>'origin' = 'neighbour'),
                   count(DISTINCT h3_cell),
                   count(*) FILTER (WHERE simulated)
              FROM risk_cells
        """)
        total, with_prob, partial, borrowed, cells, simulated = await cur.fetchone()
        print(f"\nrisk_cells: {total} rows over {cells} cells")
        print(f"  hazard_prob present   : {with_prob}")
        print(f"  hazard_prob NULL      : {total - with_prob}  (no coverage -- unknown, not zero)")
        print(f"  partial coverage      : {partial}  (lower bound: a hazard variable had no data)")
        print(f"  used a neighbour value: {borrowed}")
        print(f"  SIMULATED (scenario)  : {simulated}"
              + ("   <-- must be badged by the API, never shown as a forecast"
                 if simulated else "   (all rows are real observations)"))

        await cur.execute("""
            SELECT round(min(hazard_prob)::numeric,5),
                   round(percentile_cont(0.50) WITHIN GROUP (ORDER BY hazard_prob)::numeric,5),
                   round(percentile_cont(0.90) WITHIN GROUP (ORDER BY hazard_prob)::numeric,5),
                   round(percentile_cont(0.99) WITHIN GROUP (ORDER BY hazard_prob)::numeric,5),
                   round(max(hazard_prob)::numeric,5),
                   round(avg(uncertainty)::numeric,3)
              FROM risk_cells WHERE hazard_prob IS NOT NULL
        """)
        lo, p50, p90, p99, hi, unc = await cur.fetchone()
        print(f"\n  hazard_prob  min={lo}  p50={p50}  p90={p90}  p99={p99}  max={hi}")
        print(f"  mean uncertainty = {unc}")


async def run(hours: int) -> int:
    await open_pool()
    try:
        now = datetime.now(timezone.utc)
        t0 = now - timedelta(hours=6)
        t1 = now + timedelta(hours=hours)

        async with get_conn() as conn:
            cells, times = await observed_cells_and_times(conn, t0, t1)
            if not cells:
                print("no observations in window; run jobs.ingest_forecast first")
                return 1

            # Fetch the cells AND their neighbours in one query: a cell missing
            # a variable borrows from adjacent cells, so those rows must be in
            # the working set. See core/risk._estimate.
            working_set = sorted({c for cell in cells for c in disk(cell, 1)})
            rows = await fetch_observations(
                conn, working_set, t0, t1, [h.variable for h in HAZARDS]
            )
            print(f"cells with observations: {len(cells)}  "
                  f"(working set incl. neighbours: {len(working_set)})")
            print(f"time steps: {len(times)}   observations loaded: {len(rows)}")

            index = index_observations(rows)
            field = compute_risk_field(index, cells, times, now=now)
            written = await write_risk_cells(conn, field)
            print(f"computed and upserted {written} risk cells")

            await report(conn)
        return 0
    finally:
        await close_pool()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hours", type=int, default=72, help="forecast horizon to compute")
    args = p.parse_args(argv)
    logging.basicConfig(level=settings.log_level, format="%(levelname)-7s %(name)s: %(message)s")
    return asyncio.run(run(args.hours))


if __name__ == "__main__":
    sys.exit(main())
