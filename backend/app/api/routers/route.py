"""POST /api/route -- plan a passage across the moving hazard field."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.agents.places import UnknownPlace, resolve as resolve_place
from app.core.grid import cell_for
from app.core.routing import (
    ALPHA,
    DEFAULT_MAX_HAZARD,
    VesselConstraints,
    build_graph,
    find_route,
)
from app.db import get_conn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["route"])

#: Transit speeds by class, matching adapters/aisstream and scenarios/fleet.
CLASS_SPEEDS = {
    "frp_outboard": 4.0, "small_mech": 3.1, "trawler": 2.4,
    "fishing": 3.6, "cargo": 6.2, "tanker": 5.7, "passenger": 7.2,
}


class RouteRequest(BaseModel):
    from_: str = Field(alias="from", description="place name or 'lat,lon'")
    to: str = Field(description="place name or 'lat,lon'")
    vessel_class: str | None = None
    speed_ms: float | None = Field(default=None, gt=0, le=20)
    draft_m: float | None = Field(default=None, gt=0, le=30)
    depart_time: datetime | None = None
    max_hazard_prob: float = Field(default=DEFAULT_MAX_HAZARD, gt=0, le=1)

    model_config = {"populate_by_name": True}


class RouteResponse(BaseModel):
    found: bool
    from_cell: str
    to_cell: str
    depart_time: datetime
    eta: datetime | None = None
    duration_h: float | None = None
    distance_nm: float | None = None
    #: Probability-hours. "One hour at 50%" == "two hours at 25%".
    hazard_exposure: float | None = None
    max_hazard_on_route: float | None = None
    #: GeoJSON LineString coordinates, [lon, lat].
    coordinates: list[list[float]] = Field(default_factory=list)
    cells: list[str] = Field(default_factory=list)

    binding_constraints: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    #: True when the obstruction was HAZARD, which moves, rather than geography,
    #: which does not. NOT a claim that waiting would work -- nothing here
    #: searched for a wait-then-go solution.
    waiting_might_help: bool = False

    #: REQUIRED and always False. Draught is checked against the destination
    #: harbour only; depth along the track is not verified by anything.
    under_keel_checked: bool
    #: REQUIRED. True when the hazard field came from scenario data.
    simulated: bool
    alpha: float
    max_hazard_prob: float
    graph_cells: int
    search_ms: int


@router.post("/route", response_model=RouteResponse)
async def plan_route(req: RouteRequest) -> RouteResponse:
    try:
        origin = resolve_place(req.from_)
        dest = resolve_place(req.to)
    except UnknownPlace as e:
        raise HTTPException(400, str(e))

    speed = req.speed_ms or CLASS_SPEEDS.get(req.vessel_class or "", 3.5)
    depart = req.depart_time or datetime.now(timezone.utc)
    if depart.tzinfo is None:
        depart = depart.replace(tzinfo=timezone.utc)

    from_cell, to_cell = cell_for(origin.lat, origin.lon), cell_for(dest.lat, dest.lon)

    t0 = time.perf_counter()
    async with get_conn() as conn:
        graph = await build_graph(conn, depart)
        # The destination may be a harbour cell with no marine data -- a
        # harbour is partly land by definition.
        graph.goal_exempt.add(to_cell)
        r = find_route(graph, from_cell, to_cell, depart,
                       VesselConstraints(speed_ms=speed, draft_m=req.draft_m,
                                         max_hazard_prob=req.max_hazard_prob))
    ms = round((time.perf_counter() - t0) * 1000)

    return RouteResponse(
        found=r.found, from_cell=from_cell, to_cell=to_cell,
        depart_time=depart, eta=r.eta, duration_h=r.duration_h,
        distance_nm=r.distance_nm, hazard_exposure=r.hazard_exposure,
        max_hazard_on_route=r.max_hazard_on_route,
        coordinates=r.coordinates, cells=r.cells,
        binding_constraints=r.binding_constraints,
        blocked_reason=r.blocked_reason,
        waiting_might_help=r.waiting_might_help,
        under_keel_checked=r.under_keel_checked,
        simulated=r.simulated, alpha=ALPHA,
        max_hazard_prob=req.max_hazard_prob,
        graph_cells=len(graph.navigable), search_ms=ms,
    )
