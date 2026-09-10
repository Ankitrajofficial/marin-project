"""Area of interest: WHERE we pull data for.

One definition, imported by every ingest job. Swapping the demo point set for
a real coverage grid means changing this file and nothing else.

Two modes:
  KERALA_TN_COAST   a hand-placed 20-point set for the demo (below)
  points_from_bbox  H3 polyfill of a bounding box -- the real coverage grid,
                    already usable, just expensive over a large box
"""

from __future__ import annotations

import logging
from typing import NamedTuple

import h3

from app.config import H3_RESOLUTION

log = logging.getLogger(__name__)


class Point(NamedTuple):
    lat: float
    lon: float
    label: str = ""


# ---------------------------------------------------------------------------
# Demo point set: 20 points along the Kerala -> Tamil Nadu coast, running
# anticlockwise from the Kerala/Karnataka border, round Kanyakumari, up to
# Chennai.
#
# Two constraints shaped these, both learned the hard way:
#
#   OFFSHORE. Every point sits ~15-25 km off the coastline, in open water. The
#   marine model has no data on land and returns nulls there, so a point placed
#   on the coast silently contributes nothing.
#
#   >=15 km APART. Dedup is keyed on h3_cell, not on the point we requested.
#   Res 5 cells are ~9 km across, so two points in one cell collide on
#   (source_id, h3_cell, variable, valid_time) and the second silently upserts
#   over the first. Spacing keeps every point in its own cell, so row counts
#   stay equal to points x hours x variables instead of mysteriously short.
#   aoi_selftest() below checks both claims.
KERALA_TN_COAST: tuple[Point, ...] = (
    # -- Arabian Sea, west coast, north -> south --------------------------
    Point(12.50, 74.80, "off Kasaragod"),
    Point(12.00, 74.95, "off Kannur"),
    Point(11.50, 75.35, "off Vadakara"),
    Point(11.00, 75.60, "off Kozhikode"),
    Point(10.50, 75.80, "off Ponnani"),
    Point(10.00, 76.00, "off Kochi"),
    Point(9.50, 76.10, "off Alappuzha"),
    Point(9.00, 76.25, "off Kollam"),
    Point(8.50, 76.70, "off Thiruvananthapuram"),
    Point(8.10, 77.10, "off Kanyakumari (west)"),
    # -- Gulf of Mannar / Bay of Bengal, east coast, south -> north --------
    Point(8.30, 78.10, "off Tiruchendur"),
    Point(8.80, 78.40, "off Tuticorin"),
    Point(9.30, 79.30, "Gulf of Mannar"),
    Point(9.80, 79.55, "off Rameswaram"),
    Point(10.30, 80.05, "Palk Strait approach"),
    Point(10.80, 80.05, "off Nagapattinam"),
    Point(11.30, 80.00, "off Karaikal"),
    Point(11.80, 80.00, "off Cuddalore"),
    Point(12.30, 80.25, "off Puducherry"),
    Point(12.90, 80.50, "off Chennai"),
)

# A bbox polyfill can explode: at res 5 a 10x10 degree box is ~5,000 cells,
# which is 5,000 points of forecast for every variable. Guard rather than
# discover it as a 20-minute job and a rate limit.
MAX_BBOX_CELLS = 500


def points_from_bbox(
    south: float,
    west: float,
    north: float,
    east: float,
    resolution: int = H3_RESOLUTION,
    max_cells: int = MAX_BBOX_CELLS,
) -> list[Point]:
    """H3 polyfill of a lat/lon box, returned as cell-centroid points.

    This IS the real coverage grid -- one point per H3 cell at the project
    resolution, so no two points can collide on h3_cell by construction.

    The polyfill itself lives in core.grid.coverage_cells; this only turns
    cells into the Point shape adapters take. One implementation of the H3
    maths, not two that can drift apart.

    Land is not filtered. It does not need to be: the marine API returns null
    over land and the adapter drops missing readings, so land cells cost one
    slot in a batched request and produce no rows.
    """
    from app.core.grid import centroid, coverage_cells

    cells = coverage_cells(
        south=south, west=west, north=north, east=east,
        resolution=resolution, max_cells=max_cells,
    )
    points = []
    for cell in cells:
        lat, lon = centroid(cell)
        points.append(Point(round(lat, 6), round(lon, 6), cell))
    return points


# ---------------------------------------------------------------------------
# Water mask.
#
# A bbox polyfill is a rectangle, and this rectangle is more than half land:
# peninsular India and most of Sri Lanka sit inside it. Those cells are not
# harmless. The marine endpoint returns null over land so they produce no rows,
# but Open-Meteo's free quota is WEIGHTED BY LOCATION -- a land cell costs the
# same allowance as a sea cell and buys nothing. Of the 2,160 cells in the
# Kerala->Tamil Nadu box, 922 are at sea: masking is a 57% cut in quota spend
# for zero loss of data.
#
# THE MASK IS NOT A NEW DATASET. It is the MarineRegions maritime zones already
# loaded in hazard_zones by jobs/ingest_zones.py. An EEZ / territorial sea /
# contiguous zone polygon is water by construction -- that is what the boundary
# means -- so "centroid inside one of them" is a water test with no coastline
# file, no new dependency, and no hand-drawn polygon to drift out of date.
#
# WHAT IT DELIBERATELY EXCLUDES: foreign waters. Sri Lanka's side of the Palk
# Strait is outside the Indian EEZ and so gets no coverage. That is a coverage
# limit, not a hazard claim: an uncomputed cell is absent from the map, and
# core/risk.py never reports absence as calm.
MARINE_ZONE_TYPES = ("eez", "territorial_sea", "contiguous_zone")


async def keep_points_at_sea(points: list[Point]) -> list[Point]:
    """Filter to points inside India's maritime zones. Requires an open pool.

    Falls back to returning every point, loudly, if no marine zone has been
    loaded yet -- a masked-to-nothing ingest that silently writes no rows would
    look exactly like a broken API, and jobs/ingest_zones.py may simply not have
    been run.
    """
    from app.db import get_conn

    async with get_conn() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            WITH sea AS (
                SELECT ST_Union(geom) AS g
                  FROM hazard_zones
                 WHERE zone_type = ANY(%s)
            )
            SELECT p.i
              FROM unnest(%s::int[], %s::float8[], %s::float8[]) AS p(i, lat, lon),
                   sea
             WHERE sea.g IS NOT NULL
               AND ST_Contains(sea.g, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326))
            """,
            (list(MARINE_ZONE_TYPES), list(range(len(points))),
             [p.lat for p in points], [p.lon for p in points]),
        )
        keep = {r[0] for r in await cur.fetchall()}

    if not keep:
        log.warning(
            "water mask matched no points: hazard_zones has no %s polygon. "
            "Run jobs.ingest_zones first. Ingesting all %d points unmasked.",
            "/".join(MARINE_ZONE_TYPES), len(points),
        )
        return points
    return [p for i, p in enumerate(points) if i in keep]


def aoi_selftest(points: tuple[Point, ...] = KERALA_TN_COAST) -> dict:
    """Check the two properties the demo point set claims: distinct cells, and
    minimum separation. Cheap enough to run at job startup.
    """
    cells = [h3.latlng_to_cell(p.lat, p.lon, H3_RESOLUTION) for p in points]
    collisions = len(cells) - len(set(cells))

    min_km, pair = float("inf"), None
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            d = h3.great_circle_distance(
                (points[i].lat, points[i].lon), (points[j].lat, points[j].lon), unit="km"
            )
            if d < min_km:
                min_km, pair = d, (points[i].label, points[j].label)

    return {
        "n_points": len(points),
        "n_distinct_cells": len(set(cells)),
        "cell_collisions": collisions,
        "min_separation_km": round(min_km, 1),
        "closest_pair": pair,
    }
