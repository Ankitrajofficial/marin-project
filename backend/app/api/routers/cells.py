"""GET /api/cells/{h3_cell}/trace -- what actually produced a number.

The endpoint behind CLAUDE.md's hard rule: every number is traceable to the
observations that made it, with their source and their age.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from app.adapters.base import IssuedTimeKind
from app.api.routers.risk import snap_time
from app.api.schemas import CellTrace, TraceObservation
from app.config import SCENARIO_SOURCE_IDS
from app.core.grid import disk, fetch_observations
from app.core.risk import HAZARDS
from app.db import get_conn

router = APIRouter(prefix="/api/cells", tags=["cells"])


@router.get("/{h3_cell}/trace", response_model=CellTrace)
async def cell_trace(
    h3_cell: str,
    time: datetime | None = Query(None, description="ISO8601; snapped to nearest hour"),
) -> CellTrace:
    now = datetime.now(timezone.utc)

    async with get_conn() as conn:
        when = await snap_time(conn, time)
        if when is None:
            raise HTTPException(503, "no risk cells computed yet")

        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT hazard_prob, uncertainty, simulated, drivers
                     FROM risk_cells WHERE valid_time = %s AND h3_cell = %s""",
                (when, h3_cell),
            )
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, f"no risk cell {h3_cell} at {when.isoformat()}")
        hazard_prob, uncertainty, simulated, drivers = row
        drivers = drivers or {}

        # The cell AND its neighbours: a cell missing a variable borrows from
        # adjacent cells, so a trace listing only this cell's own observations
        # would fail to explain where the borrowed number came from -- which is
        # exactly the thing a trace exists to explain.
        # fetch_observations takes a half-open [t0, t1) window; we want exactly
        # one instant, so t1 is one second past it.
        rows = await fetch_observations(
            conn,
            disk(h3_cell, 1),
            when,
            when + timedelta(seconds=1),
            [h.variable for h in HAZARDS],
        )

    observations: list[TraceObservation] = []
    for r in rows:
        is_proxy = r.issued_time_kind == IssuedTimeKind.FETCH_PROXY.value
        age = (now - r.issued_time).total_seconds() if r.issued_time else None
        observations.append(
            TraceObservation(
                source_id=r.source_id,
                variable=r.variable.value,
                value=r.value,
                unit=r.unit,
                valid_time=r.valid_time,
                issued_time=r.issued_time,
                issued_time_kind=r.issued_time_kind,
                age_seconds=age,
                age_is_lower_bound=is_proxy,
                origin="self" if r.h3_cell == h3_cell else "neighbour",
                from_cell=r.h3_cell,
                reliability=r.reliability,
                simulated=r.source_id in SCENARIO_SOURCE_IDS,
            )
        )

    observations.sort(key=lambda o: (o.origin != "self", o.variable, o.source_id))

    return CellTrace(
        h3_cell=h3_cell,
        valid_time=when,
        requested_time=time,
        hazard_prob=hazard_prob,
        uncertainty=uncertainty,
        simulated=simulated,
        simulated_sources=drivers.get("simulated_sources", []),
        drivers=drivers,
        observations=observations,
    )
