"""GET /api/risk -- the hazard field as GeoJSON."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    RiskFeature,
    RiskFeatureCollection,
    RiskProperties,
    RiskTimes,
)
from app.core.grid import cell_boundary_geojson, filter_cells_in_bbox
from app.db import get_conn

router = APIRouter(prefix="/api/risk", tags=["risk"])

# Cap on features in one response. A guard on payload size, not on how much
# geography the client asked about -- a huge empty ocean box is cheap, a small
# box over a fully-computed coast is not.
MAX_FEATURES = 5000


async def snap_time(conn, requested: datetime | None) -> datetime | None:
    """Nearest available valid_time to `requested` (or to now if None).

    Snapping rather than exact-matching because the field is hourly and a
    client asking for 14:37 means "the hour around 14:37". The chosen time is
    returned to the caller so the map can never be labelled with a different
    hour than it is showing.
    """
    target = requested or datetime.now(timezone.utc)
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT valid_time FROM risk_cells
                ORDER BY abs(EXTRACT(EPOCH FROM (valid_time - %s))) LIMIT 1""",
            (target,),
        )
        row = await cur.fetchone()
    return row[0] if row else None


@router.get("/times", response_model=RiskTimes)
async def risk_times() -> RiskTimes:
    """Every time step with computed risk. Drives the frontend slider."""
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT DISTINCT valid_time FROM risk_cells ORDER BY 1")
            times = [r[0] for r in await cur.fetchall()]
    return RiskTimes(
        times=times,
        count=len(times),
        first=times[0] if times else None,
        last=times[-1] if times else None,
    )


@router.get("", response_model=RiskFeatureCollection)
async def risk_field(
    bbox: str = Query(
        ...,
        description="west,south,east,north in degrees (lon-first, as web map "
                    "clients send it)",
        examples=["74.0,7.5,81.5,13.5"],
    ),
    time: datetime | None = Query(
        None, description="ISO8601; snapped to the nearest available hour"
    ),
) -> RiskFeatureCollection:
    try:
        west, south, east, north = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be four numbers: west,south,east,north")

    # Catch a lat/lon swap at the door. A client sending south,west,north,east
    # produces a plausible-looking box that quietly covers the wrong ocean.
    if not (-180 <= west < east <= 180):
        raise HTTPException(400, f"bad longitudes: west={west} east={east} "
                                 f"(bbox order is west,south,east,north)")
    if not (-90 <= south < north <= 90):
        raise HTTPException(400, f"bad latitudes: south={south} north={north} "
                                 f"(bbox order is west,south,east,north)")

    async with get_conn() as conn:
        when = await snap_time(conn, time)
        if when is None:
            raise HTTPException(
                503, "no risk cells computed yet; run jobs.compute_risk"
            )
        # Query by time (the hypertable's partition key, so this is a chunk
        # lookup) and filter by geography in Python. The reverse -- polyfilling
        # the viewport into a cell list and passing it to `= ANY` -- costs the
        # AREA OF THE VIEWPORT: a zoomed-out Indian Ocean is ~56,000 cells at
        # res 5, almost none of which have been computed. This way the cost
        # tracks the data that exists.
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT h3_cell, hazard_prob, uncertainty, simulated, drivers
                     FROM risk_cells
                    WHERE valid_time = %s
                    ORDER BY h3_cell""",
                (when,),
            )
            rows = await cur.fetchall()

    keep = set(filter_cells_in_bbox([r[0] for r in rows], west, south, east, north))
    rows = [r for r in rows if r[0] in keep]

    if len(rows) > MAX_FEATURES:
        raise HTTPException(
            400,
            f"{len(rows)} cells in view, over the {MAX_FEATURES} feature limit "
            f"-- zoom in. (The limit is response size, not geography: it "
            f"depends on how much risk has been computed, not how big the box is.)",
        )

    features: list[RiskFeature] = []
    for h3_cell, hazard_prob, uncertainty, simulated, drivers in rows:
        drivers = drivers or {}
        features.append(
            RiskFeature(
                geometry={
                    "type": "Polygon",
                    "coordinates": [cell_boundary_geojson(h3_cell)],
                },
                properties=RiskProperties(
                    h3_cell=h3_cell,
                    hazard_prob=hazard_prob,
                    uncertainty=uncertainty,
                    # Straight from the NOT NULL column. Never defaulted,
                    # never inferred from drivers -- the column is the guard.
                    simulated=simulated,
                    simulated_sources=drivers.get("simulated_sources", []),
                    drivers=drivers,
                ),
            )
        )

    probs = [f.properties.hazard_prob for f in features
             if f.properties.hazard_prob is not None]

    return RiskFeatureCollection(
        valid_time=when,
        requested_time=time,
        # Collection-level guard: true if ANY feature is simulated.
        simulated=any(f.properties.simulated for f in features),
        n_features=len(features),
        hazard_prob_max=max(probs) if probs else None,
        features=features,
    )
