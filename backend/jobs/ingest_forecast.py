"""Ingest job: pull Open-Meteo forecasts for an area of interest.

    python -m jobs.ingest_forecast                          # demo AOI
    python -m jobs.ingest_forecast --bbox 8.0 76.0 9.0 77.0 # H3 coverage grid
    python -m jobs.ingest_forecast --forecast-days 5

Runs both Open-Meteo adapters and reports rows written per variable. Reads
counts back from the database afterwards rather than trusting its own
in-memory tally, so an upsert that replaced rows instead of adding them shows
up as what it is.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter

from app.adapters.base import Observation, fetch_reliability
from app.adapters.open_meteo import OpenMeteoMarineAdapter, OpenMeteoWeatherAdapter
from app.aoi import KERALA_TN_COAST, Point, aoi_selftest, points_from_bbox
from app.config import settings
from app.db import close_pool, get_conn, open_pool

log = logging.getLogger("ingest_forecast")

ADAPTERS = (OpenMeteoMarineAdapter, OpenMeteoWeatherAdapter)


async def db_counts() -> list[tuple[str, str, int, int, str, str]]:
    """Per source+variable: rows, distinct cells, and the valid_time span."""
    sql = """
        SELECT source_id,
               variable,
               count(*)                          AS rows,
               count(DISTINCT h3_cell)           AS cells,
               to_char(min(valid_time) AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS first_t,
               to_char(max(valid_time) AT TIME ZONE 'UTC', 'MM-DD HH24:MI') AS last_t
          FROM observations
      GROUP BY source_id, variable
      ORDER BY source_id, variable
    """
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql)
            return await cur.fetchall()


def report(title: str, rows: list[tuple]) -> None:
    print(f"\n{title}")
    print(f"  {'source':<18} {'variable':<18} {'rows':>7} {'cells':>6}  valid_time span (UTC)")
    print(f"  {'-'*18} {'-'*18} {'-'*7} {'-'*6}  {'-'*26}")
    total = 0
    for source_id, variable, n, cells, first_t, last_t in rows:
        total += n
        print(f"  {source_id:<18} {variable:<18} {n:>7} {cells:>6}  {first_t} -> {last_t}")
    print(f"  {'':<18} {'TOTAL':<18} {total:>7}")


async def run(points: list[Point], forecast_days: int) -> int:
    await open_pool()
    try:
        checks = aoi_selftest(tuple(points))
        print(f"AOI: {checks['n_points']} points, {checks['n_distinct_cells']} distinct "
              f"H3 cells, closest pair {checks['min_separation_km']} km")
        if checks["cell_collisions"]:
            # Not fatal, but the row counts below will look short and this is
            # the only place that explains why.
            log.warning(
                "%d points share an H3 cell with another point; they will "
                "upsert over each other rather than adding rows: %s",
                checks["cell_collisions"], checks["closest_pair"],
            )

        written_by_variable: Counter[str] = Counter()
        for adapter_cls in ADAPTERS:
            reliability = await fetch_reliability(adapter_cls.source_id)
            adapter = adapter_cls(points, confidence=reliability, forecast_days=forecast_days)

            print(f"\n{adapter_cls.source_id}: fetching {len(points)} points, "
                  f"{forecast_days}d, confidence={reliability} (from sources.reliability)")

            raw = await adapter.fetch()
            observations: list[Observation] = adapter.normalize(raw)
            for obs in observations:
                written_by_variable[obs.variable.value] += 1

            n = await adapter.write(observations)
            print(f"  normalized {len(observations)} observations, upserted {n}")

        report("Rows in observations, read back from the database:", await db_counts())
        return 0
    finally:
        await close_pool()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"),
                   help="bounding box; H3-polyfilled at the project resolution. "
                        "Default is the demo AOI in app/aoi.py")
    p.add_argument("--forecast-days", type=int, default=3)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    points = list(points_from_bbox(*args.bbox)) if args.bbox else list(KERALA_TN_COAST)
    return asyncio.run(run(points, args.forecast_days))


if __name__ == "__main__":
    sys.exit(main())
