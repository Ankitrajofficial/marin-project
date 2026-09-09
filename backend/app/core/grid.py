"""The spatial index everything in core/ shares.

ORCA discretizes the ocean onto H3 cells. H3 is used rather than a lat/lon
rectangle grid for two reasons that matter downstream:

  * Hexagons have SIX equidistant neighbours. A square grid has four at
    distance d and four at distance d*sqrt(2), so a route stepping diagonally
    across a square grid accumulates a systematic length error, and a hazard
    "spreading" across one biases along the axes. core/routing.py steps over
    this grid, so that distortion would end up in an evacuation route.
  * Cell area is near-constant with latitude. A 0.1-degree lat/lon box at
    Kanyakumari (8N) and at Kandla (23N) differ in area by ~15%, which would
    silently weight risk by latitude.

Every function here is pure except fetch_observations, which reads the
database and takes an open connection rather than opening one -- core/ does
not own connection lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import h3

from app.adapters.base import Variable
from app.config import H3_RESOLUTION

# Re-exported so core/ modules import the resolution from the grid they are
# working on, rather than each reaching into config independently.
__all__ = [
    "H3_RESOLUTION",
    "ObsRow",
    "cell_for",
    "centroid",
    "neighbours",
    "disk",
    "grid_distance",
    "coverage_cells",
    "cells_to_latlng",
    "cell_boundary_geojson",
    "cell_in_bbox",
    "filter_cells_in_bbox",
    "fetch_observations",
    "index_observations",
]

# A bbox polyfill grows as the square of the box size. At res 5 a 10x10 degree
# box is several thousand cells, each one a point in every forecast request.
MAX_COVERAGE_CELLS = 500


# ---------------------------------------------------------------------------
# Cell arithmetic
# ---------------------------------------------------------------------------
def cell_for(lat: float, lon: float, resolution: int = H3_RESOLUTION) -> str:
    """(lat, lon) -> H3 cell. Note the argument order: h3 v4 takes lat first,
    while GeoJSON, PostGIS ST_MakePoint and most map libraries take lon first.
    """
    return h3.latlng_to_cell(lat, lon, resolution)


def centroid(cell: str) -> tuple[float, float]:
    """H3 cell -> (lat, lon) of its centre."""
    return h3.cell_to_latlng(cell)


def disk(cell: str, k: int = 1) -> list[str]:
    """The cell and everything within k steps of it (h3 calls this a k-ring)."""
    return sorted(h3.grid_disk(cell, k))


def neighbours(cell: str, k: int = 1) -> list[str]:
    """Everything within k steps, EXCLUDING the cell itself.

    Separate from disk() because the distinction matters to risk.py: a value
    measured in this cell and a value borrowed from a neighbour are not the
    same evidence and must not be pooled as if they were.
    """
    return sorted(c for c in h3.grid_disk(cell, k) if c != cell)


def grid_distance(a: str, b: str) -> int:
    """Steps between two cells. 0 = same cell, 1 = adjacent."""
    return h3.grid_distance(a, b)


def cells_to_latlng(cells: Iterable[str]) -> list[tuple[float, float]]:
    return [h3.cell_to_latlng(c) for c in cells]


def cell_boundary_geojson(cell: str) -> list[list[float]]:
    """H3 cell -> a GeoJSON linear ring: closed, [lon, lat], right-hand wound.

    Three conversions happen here, each of which is silently wrong-looking
    rather than crash-y if skipped:

      * h3 returns (lat, lon) pairs; GeoJSON positions are [lon, lat]. Getting
        this backwards puts the Bay of Bengal in Somalia and still renders.
      * A GeoJSON ring must repeat its first position as its last.
      * RFC 7946 wants exterior rings counter-clockwise. Most renderers do not
        care, but tools that do (turf, PostGIS ST_GeomFromGeoJSON in strict
        mode) treat a clockwise exterior as a hole. Cheap to just get right.
    """
    ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(cell)]

    # Shoelace: positive area = counter-clockwise in [lon, lat] space.
    area2 = sum(
        ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
        for i in range(len(ring))
    )
    if area2 < 0:
        ring.reverse()

    ring.append(ring[0])
    return ring


def cell_in_bbox(cell: str, west: float, south: float, east: float, north: float) -> bool:
    """Is this cell's centre inside a WEB-ORDER bbox (west, south, east, north)?

    Note the argument order differs from coverage_cells(south, west, north,
    east). Web clients (Leaflet, MapLibre, OGC) speak lon-first; H3 and this
    module speak lat-first. The two orders meet here and nowhere else.
    """
    lat, lon = h3.cell_to_latlng(cell)
    return south <= lat <= north and west <= lon <= east


def filter_cells_in_bbox(
    cells: Iterable[str], west: float, south: float, east: float, north: float
) -> list[str]:
    """Keep the cells whose centres fall in the box.

    The API filters ALREADY-COMPUTED cells this way rather than polyfilling the
    viewport, because polyfill cost scales with the AREA OF THE VIEWPORT while
    this scales with the amount of data that actually exists. A zoomed-out map
    of the Indian Ocean polyfills to ~56,000 cells at res 5, almost none of
    which have been computed -- so the expensive version of the question is
    also the useless one.
    """
    return [c for c in cells if cell_in_bbox(c, west, south, east, north)]


def coverage_cells(
    south: float,
    west: float,
    north: float,
    east: float,
    resolution: int = H3_RESOLUTION,
    max_cells: int = MAX_COVERAGE_CELLS,
) -> list[str]:
    """Every H3 cell whose centre falls in the bounding box.

    This is the real coverage grid: one cell per unit of the model's spatial
    resolution, so no two sample points can collide on h3_cell by construction.

    Land is NOT filtered. It does not need to be for ingest -- marine sources
    return null over land and adapters drop missing readings, so a land cell
    costs one slot in a batched request and produces no rows. It does mean a
    land cell shows up here as a cell with no data, which risk.py reports as
    absent coverage rather than as calm water.
    """
    if south >= north or west >= east:
        raise ValueError(f"degenerate bbox: S={south} W={west} N={north} E={east}")

    poly = h3.LatLngPoly([(south, west), (south, east), (north, east), (north, west)])
    cells = sorted(h3.polygon_to_cells(poly, resolution))

    if len(cells) > max_cells:
        raise ValueError(
            f"bbox covers {len(cells)} cells at res {resolution}, over the "
            f"{max_cells} guard. Shrink the box or raise max_cells knowingly."
        )
    return cells


# ---------------------------------------------------------------------------
# Reading observations
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ObsRow:
    """One observation as core/ sees it.

    Carries the source's reliability prior joined in, because every consumer
    (fusion, risk) needs it to weight the value, and re-querying per row would
    be absurd. Carries issued_time_kind because data-age means different
    things depending on it -- see adapters/base.IssuedTimeKind.
    """

    h3_cell: str
    variable: Variable
    valid_time: datetime
    value: float
    unit: str
    source_id: str
    reliability: float
    confidence: float | None
    issued_time: datetime | None
    issued_time_kind: str | None


_OBS_SQL = """
    SELECT o.h3_cell, o.variable, o.valid_time, o.value, o.unit,
           o.source_id, s.reliability, o.confidence,
           o.issued_time, o.issued_time_kind
      FROM observations o
      JOIN sources s ON s.source_id = o.source_id
     WHERE o.h3_cell = ANY(%(cells)s)
       AND o.valid_time >= %(t0)s
       AND o.valid_time <  %(t1)s
       {variable_filter}
     ORDER BY o.valid_time, o.h3_cell, o.variable
"""


async def fetch_observations(
    conn: Any,
    cells: Sequence[str],
    t0: datetime,
    t1: datetime,
    variables: Sequence[Variable] | None = None,
) -> list[ObsRow]:
    """Observations for a set of cells over [t0, t1).

    Takes an open connection instead of opening one: core/ is algorithms, and
    a module that manages its own pool cannot be called inside someone else's
    transaction.

    Callers wanting a cell plus its neighbours pass disk(cell, k) as `cells`.
    Fetching the whole working set in ONE query and indexing it in memory
    (see index_observations) is deliberate -- the per-cell alternative is
    thousands of round trips to answer the same question.
    """
    params: dict[str, Any] = {"cells": list(cells), "t0": t0, "t1": t1}
    if variables:
        sql = _OBS_SQL.format(variable_filter="AND o.variable = ANY(%(variables)s)")
        params["variables"] = [v.value for v in variables]
    else:
        sql = _OBS_SQL.format(variable_filter="")

    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        rows = await cur.fetchall()

    return [
        ObsRow(
            h3_cell=r[0],
            variable=Variable(r[1]),
            valid_time=r[2],
            value=float(r[3]),
            unit=r[4],
            source_id=r[5],
            reliability=float(r[6]),
            confidence=float(r[7]) if r[7] is not None else None,
            issued_time=r[8],
            issued_time_kind=r[9],
        )
        for r in rows
    ]


def index_observations(
    rows: Iterable[ObsRow],
) -> dict[tuple[str, datetime, Variable], list[ObsRow]]:
    """Group observations by (cell, time, variable).

    The lookup shape risk.py needs: for one cell at one instant, all sources
    that have something to say about one variable. A list rather than a single
    value because two sources reporting the same thing is the interesting case
    -- their disagreement is the only real uncertainty estimate available.
    """
    index: dict[tuple[str, datetime, Variable], list[ObsRow]] = {}
    for row in rows:
        index.setdefault((row.h3_cell, row.valid_time, row.variable), []).append(row)
    return index
