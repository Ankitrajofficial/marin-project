"""GET /api/recall -- vessel recall triage, ranked by slack.

Thin: core/recall.py does the arithmetic, this serialises it and attaches the
caveats that make a thin result readable as thin data rather than good news.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import HarbourOut, RecallEntryOut, RecallResponse
from app.core.recall import (
    DEFAULT_THRESHOLD,
    DETOUR_FACTOR,
    RecallEntry,
    build,
    gather,
)
from app.core.routing import VesselConstraints, build_graph, find_route
from app.core.grid import cell_for
from app.db import get_conn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["recall"])

#: Below this a ranking is not a ranking, it is a list. Said out loud rather
#: than presented as a prioritised fleet.
MEANINGFUL_FLEET = 5


def _out(e: RecallEntry) -> RecallEntryOut:
    return RecallEntryOut(
        mmsi=e.mmsi, name=e.name, vessel_class=e.vessel_class,
        lat=e.lat, lon=e.lon, h3_cell=e.h3_cell, draft_m=e.draft_m,
        position_time=e.position_time,
        position_age_minutes=round(e.position_age_s / 60, 1),
        position_is_stale=e.position_is_stale,
        position_uncertainty_nm=round(e.position_uncertainty_nm, 2),
        speed_ms=round(e.speed_ms, 2), speed_source=e.speed_source,
        speed_samples=e.speed_samples,
        harbour=HarbourOut(
            harbour_id=e.harbour.harbour_id, name=e.harbour.name,
            lat=e.harbour.lat, lon=e.harbour.lon,
            harbour_type=e.harbour.harbour_type,
            distance_nm=round(e.harbour.distance_nm, 2),
            depth_source=e.harbour.depth_source, depth_m=e.harbour.depth_m,
            draft_ok=e.harbour.draft_ok,
        ) if e.harbour else None,
        time_to_harbour_h=round(e.time_to_harbour_h, 2) if e.time_to_harbour_h is not None else None,
        time_is_routed=e.time_is_routed,
        route_coordinates=e.route_coordinates,
        route_distance_nm=e.route_distance_nm,
        route_exposure=e.route_exposure,
        route_max_hazard=e.route_max_hazard,
        route_blocked_reason=e.route_blocked_reason,
        route_waiting_might_help=e.route_waiting_might_help,
        under_keel_checked=e.under_keel_checked,
        time_to_hazard_h=round(e.time_to_hazard_h, 2) if e.time_to_hazard_h is not None else None,
        hazard_time=e.hazard_time,
        hazard_prob_at_crossing=e.hazard_prob_at_crossing,
        hazard_driver=e.hazard_driver,
        hazard_data_age_minutes=round(e.hazard_data_age_s / 60, 1) if e.hazard_data_age_s is not None else None,
        margin_h=round(e.margin_h, 2) if e.margin_h is not None else None,
        status=e.status, reasons=e.reasons, flags=e.flags, simulated=e.simulated,
    )


@router.get("/recall", response_model=RecallResponse)
async def recall(
    bbox: str | None = Query(None, description="west,south,east,north"),
    threshold: float = Query(DEFAULT_THRESHOLD, gt=0, le=1,
                             description="hazard_prob at which a cell counts as dangerous"),
    lookback_h: float = Query(24.0, gt=0, le=168),
) -> RecallResponse:
    box = None
    if bbox:
        try:
            w, s, e, n = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be west,south,east,north")
        if not (-180 <= w < e <= 180) or not (-90 <= s < n <= 90):
            raise HTTPException(400, "bbox out of range (west,south,east,north)")
        box = (w, s, e, n)

    now = datetime.now(timezone.utc)
    async with get_conn() as conn:
        inputs = await gather(conn, now, bbox=box, lookback_h=lookback_h)

        # Build the routing graph once and route every vessel against it.
        # Timing is reported: if this becomes too slow for the fleet size, the
        # number is visible rather than the search being quietly reduced.
        t0 = time.perf_counter()
        graph = await build_graph(conn, now)
        build_ms = round((time.perf_counter() - t0) * 1000)

        def route_fn(from_cell, harbour, speed_ms, draft_m):
            if not graph.navigable:
                return None
            to_cell = cell_for(harbour.lat, harbour.lon)
            graph.goal_exempt.add(to_cell)
            return find_route(graph, from_cell, to_cell, now,
                              VesselConstraints(speed_ms=speed_ms,
                                                draft_m=draft_m))

        t1 = time.perf_counter()
        ranked, unknown = build(inputs, now, threshold,
                                route_fn=route_fn if graph.navigable else None)
        route_ms = round((time.perf_counter() - t1) * 1000)

    caveats: list[str] = []
    n_vessels = len(inputs["vessels"])
    if n_vessels == 0:
        caveats.append(
            "No vessels are transmitting AIS in this area and time window. "
            "That is an absence of AIS coverage, not an absence of boats."
        )
    elif n_vessels < MEANINGFUL_FLEET:
        caveats.append(
            f"Only {n_vessels} vessel(s) seen. This is a list, not a "
            f"meaningful prioritisation."
        )
    caveats.append(
        "Small fishing craft are largely not required to carry AIS and mostly "
        "do not appear here — they are the vessels a cyclone recall exists "
        "for. Live AIS alone cannot represent that fleet."
    )
    if unknown:
        caveats.append(
            f"{len(unknown)} vessel(s) could not be assessed and are listed "
            f"separately. They are NOT ranked as safe."
        )
    if inputs["harbours"] and not any(h["depth_m"] for h in inputs["harbours"]):
        caveats.append(
            "No harbour in the database has a charted depth, so draught "
            "compatibility is unverified for every vessel."
        )
    all_entries = ranked + unknown
    n_routed = sum(1 for e in all_entries if e.time_is_routed)
    n_straight = len(all_entries) - n_routed

    if n_straight:
        # The caveat now names the vessels it applies to instead of blanketing
        # the whole response.
        caveats.append(
            f"{n_straight} of {len(all_entries)} vessel(s) have no safe route "
            f"to any candidate harbour; their times are straight-line "
            f"x{DETOUR_FACTOR} estimates and are OPTIMISTIC. They are flagged "
            f"no_route_found individually."
        )
    if n_routed:
        caveats.append(
            f"{n_routed} vessel(s) use a real routed time from core/routing.py, "
            f"planned against the moving hazard field with maritime boundaries "
            f"as hard constraints."
        )
    caveats.append(
        "No route checks under-keel clearance. Draught is checked against the "
        "destination harbour only; depth along the track is not verified."
    )
    return RecallResponse(
        generated_at=now, threshold=threshold, detour_factor=DETOUR_FACTOR,
        distance_is_straight_line=n_straight > 0,
        n_vessels=n_vessels, n_harbours=len(inputs["harbours"]),
        n_routed=n_routed, n_straight_line=n_straight,
        routing_available=bool(graph.navigable),
        routing_graph_cells=len(graph.navigable),
        routing_build_ms=build_ms + route_ms,
        under_keel_checked=False,
        ranked=[_out(e) for e in ranked],
        cannot_assess=[_out(e) for e in unknown],
        simulated=any(e.simulated for e in all_entries),
        caveats=caveats,
    )
