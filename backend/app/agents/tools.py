"""The ONLY things the agent may call.

Every tool: a Pydantic input schema that validates before anything reaches
core/, and a JSON-serialisable output whose every number came from a
deterministic solver. The LLM chooses WHICH tool and WHAT symbolic arguments.
It never computes a value, a coordinate or a timestamp.

The outputs of these tools are also what the number guard allowlists. If a
figure is not in a tool result, it may not appear in an answer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.adapters.base import IssuedTimeKind
from app.agents.places import Place, UnknownPlace, resolve as resolve_place
from app.agents.timespec import TIME_SYMBOLS, TimeWindow, resolve as resolve_time
from app.core.geofence import proximity_alert
from app.core.grid import cell_for, disk, fetch_observations
from app.core.risk import HAZARDS
from app.config import SCENARIO_SOURCE_IDS

log = logging.getLogger(__name__)

ToolName = Literal["get_risk", "check_boundaries", "get_conditions"]
TOOL_NAMES: tuple[str, ...] = ("get_risk", "check_boundaries", "get_conditions")

#: How far to look for a computed risk cell when the exact cell has none.
#: Reported explicitly in the output whenever it is used -- borrowed coverage
#: is never presented as coverage.
MAX_CELL_SEARCH_K = 2


# ===========================================================================
# Input schemas -- validation happens HERE, before core/ sees anything
# ===========================================================================
class RiskQuery(BaseModel):
    place: str = Field(description="named coastal place, or 'lat,lon'")
    when: str = Field(default="now", description=f"one of: {', '.join(TIME_SYMBOLS)}")

    @field_validator("when")
    @classmethod
    def _known_symbol(cls, v: str) -> str:
        if v not in TIME_SYMBOLS:
            raise ValueError(f"when must be one of {TIME_SYMBOLS}, got {v!r}")
        return v


class BoundaryQuery(BaseModel):
    place: str
    buffer_nm: float = Field(default=2.0, gt=0, le=200)


class ConditionsQuery(BaseModel):
    place: str
    when: str = Field(default="now")

    @field_validator("when")
    @classmethod
    def _known_symbol(cls, v: str) -> str:
        if v not in TIME_SYMBOLS:
            raise ValueError(f"when must be one of {TIME_SYMBOLS}, got {v!r}")
        return v


SCHEMAS: dict[str, type[BaseModel]] = {
    "get_risk": RiskQuery,
    "check_boundaries": BoundaryQuery,
    "get_conditions": ConditionsQuery,
}


# ===========================================================================
# Helpers
# ===========================================================================
def _place_block(p: Place) -> dict[str, Any]:
    return {"name": p.name, "lat": p.lat, "lon": p.lon, "note": p.note}


def _window_block(w: TimeWindow) -> dict[str, Any]:
    return {"symbol": w.symbol, "label": w.label,
            "start": w.start.isoformat(), "end": w.end.isoformat(),
            "hours": w.hours}


async def _resolve_cell(conn: Any, lat: float, lon: float, w: TimeWindow):
    """The place's own cell if risk was computed for it, else the nearest
    computed cell within MAX_CELL_SEARCH_K rings.

    Returns (cell, rings_away) or (None, None). Borrowing is reported in the
    output; it is never silently presented as coverage of the requested point.
    """
    exact = cell_for(lat, lon)
    candidates = disk(exact, MAX_CELL_SEARCH_K)
    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT DISTINCT h3_cell FROM risk_cells
                WHERE valid_time >= %s AND valid_time < %s AND h3_cell = ANY(%s)""",
            (w.start, w.end, candidates),
        )
        have = {r[0] for r in await cur.fetchall()}
    if exact in have:
        return exact, 0
    from app.core.grid import grid_distance
    ranked = sorted(have, key=lambda c: grid_distance(exact, c))
    return (ranked[0], grid_distance(exact, ranked[0])) if ranked else (None, None)


async def _sources_block(conn: Any, cell: str, w: TimeWindow, now: datetime):
    """Which sources fed this cell, and how stale each is."""
    rows = await fetch_observations(
        conn, disk(cell, 1), w.start, w.end, [h.variable for h in HAZARDS]
    )
    by_source: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = by_source.setdefault(r.source_id, {
            "source_id": r.source_id, "variables": set(), "n": 0,
            "issued_time": r.issued_time, "issued_time_kind": r.issued_time_kind,
            "reliability": r.reliability,
            "simulated": r.source_id in SCENARIO_SOURCE_IDS,
        })
        s["variables"].add(r.variable.value)
        s["n"] += 1
        if r.issued_time and (s["issued_time"] is None or r.issued_time > s["issued_time"]):
            s["issued_time"] = r.issued_time

    out = []
    for s in by_source.values():
        issued = s["issued_time"]
        out.append({
            "source_id": s["source_id"],
            "variables": sorted(s["variables"]),
            "n_observations": s["n"],
            "reliability": s["reliability"],
            "issued_time": issued.isoformat() if issued else None,
            "issued_time_kind": s["issued_time_kind"],
            "age_minutes": round((now - issued).total_seconds() / 60.0, 1) if issued else None,
            # A fetch-proxy age is a LOWER bound: the source publishes no model
            # run time, so the data is AT LEAST this old.
            "age_is_lower_bound": s["issued_time_kind"] == IssuedTimeKind.FETCH_PROXY.value,
            "simulated": s["simulated"],
        })
    return sorted(out, key=lambda s: s["source_id"])


# ===========================================================================
# Tools
# ===========================================================================
async def get_risk(conn: Any, q: RiskQuery, now: datetime | None = None) -> dict[str, Any]:
    """Exceedance probabilities for a place over a time window."""
    now = now or datetime.now(timezone.utc)
    place = resolve_place(q.place)
    w = resolve_time(q.when, now=now)

    cell, rings = await _resolve_cell(conn, place.lat, place.lon, w)
    if cell is None:
        return {
            "tool": "get_risk", "place": _place_block(place), "window": _window_block(w),
            "no_data": True,
            "reason": "no risk cell has been computed within "
                      f"{MAX_CELL_SEARCH_K} cells of this position for this window",
            "simulated": False,
        }

    async with conn.cursor() as cur:
        await cur.execute(
            """SELECT valid_time, hazard_prob, uncertainty, simulated, drivers
                 FROM risk_cells
                WHERE h3_cell = %s AND valid_time >= %s AND valid_time < %s
                ORDER BY valid_time""",
            (cell, w.start, w.end),
        )
        rows = await cur.fetchall()

    steps, probs = [], []
    peak = None
    for valid_time, hazard_prob, uncertainty, simulated, drivers in rows:
        drivers = drivers or {}
        steps.append({
            "valid_time": valid_time.isoformat(),
            "hazard_prob": hazard_prob,
            "uncertainty": uncertainty,
            "simulated": simulated,
            "variables": {
                k: {"value": v["mu"], "unit": v["unit"], "threshold": v["threshold"],
                    "p_exceed": v["p_exceed"], "origin": v["origin"]}
                for k, v in (drivers.get("variables") or {}).items()
            },
            "partial_coverage": drivers.get("partial_coverage", False),
            "missing": drivers.get("missing", []),
            "hazard_prob_if_fully_correlated":
                drivers.get("hazard_prob_if_fully_correlated"),
        })
        if hazard_prob is not None:
            probs.append(hazard_prob)
            if peak is None or hazard_prob > peak[1]:
                peak = (valid_time, hazard_prob)

    thresholds = {h.variable.value: {"threshold": h.threshold, "unit": h.unit}
                  for h in HAZARDS}

    return {
        "tool": "get_risk",
        "place": _place_block(place),
        "window": _window_block(w),
        "h3_cell": cell,
        "cell_rings_from_place": rings,
        "cell_is_exact": rings == 0,
        "n_steps": len(steps),
        "max_hazard_prob": max(probs) if probs else None,
        "min_hazard_prob": min(probs) if probs else None,
        "peak_time": peak[0].isoformat() if peak else None,
        "max_uncertainty": max((s["uncertainty"] for s in steps), default=None),
        "any_partial_coverage": any(s["partial_coverage"] for s in steps),
        "thresholds": thresholds,
        "steps": steps,
        "sources": await _sources_block(conn, cell, w, now),
        "simulated": any(s["simulated"] for s in steps),
    }


async def check_boundaries(conn: Any, q: BoundaryQuery,
                           now: datetime | None = None) -> dict[str, Any]:
    """Geofence assessment for a position."""
    place = resolve_place(q.place)
    r = await proximity_alert(conn, place.lat, place.lon, q.buffer_nm)

    def hit(h) -> dict[str, Any]:
        return {
            "zone_id": h.zone_id, "zone_type": h.zone_type, "name": h.name,
            "authority": h.authority, "verdict": h.verdict, "inside": h.inside,
            "distance_nm": round(h.distance_nm, 2),
            "effective_distance_nm": round(h.effective_distance_nm, 2),
            "margin_nm": h.margin_nm,
            "bearing_deg": round(h.bearing_deg) if h.bearing_deg is not None else None,
        }

    return {
        "tool": "check_boundaries",
        "place": _place_block(place),
        "buffer_nm": q.buffer_nm,
        "verdict": r.verdict,
        "inside": [hit(h) for h in r.inside],
        "alerts": [hit(h) for h in r.alerts],
        "nearest_by_type": {k: hit(v) for k, v in r.nearest_by_type.items()},
        "advisory_only": r.advisory_only,
        "attributions": r.attributions,
        "simulated": False,
    }


async def get_conditions(conn: Any, q: ConditionsQuery,
                         now: datetime | None = None) -> dict[str, Any]:
    """Observed/forecast values at a place, with source and data age."""
    now = now or datetime.now(timezone.utc)
    place = resolve_place(q.place)
    w = resolve_time(q.when, now=now)
    cell = cell_for(place.lat, place.lon)

    rows = await fetch_observations(conn, disk(cell, 1), w.start, w.end)
    if not rows:
        return {"tool": "get_conditions", "place": _place_block(place),
                "window": _window_block(w), "h3_cell": cell,
                "no_data": True, "observations": [], "sources": [],
                "simulated": False}

    # One value per variable: the reading nearest the window start, so
    # "conditions now" is one moment rather than an average over hours.
    best: dict[str, Any] = {}
    for r in rows:
        key = r.variable.value
        gap = abs((r.valid_time - w.start).total_seconds())
        if key not in best or gap < best[key][0]:
            best[key] = (gap, r)

    observations = []
    for key, (_, r) in sorted(best.items()):
        is_proxy = r.issued_time_kind == IssuedTimeKind.FETCH_PROXY.value
        observations.append({
            "variable": key,
            "value": r.value,
            "unit": r.unit,
            "valid_time": r.valid_time.isoformat(),
            "source_id": r.source_id,
            "reliability": r.reliability,
            "issued_time": r.issued_time.isoformat() if r.issued_time else None,
            "issued_time_kind": r.issued_time_kind,
            "age_minutes": round((now - r.issued_time).total_seconds() / 60.0, 1)
                           if r.issued_time else None,
            "age_is_lower_bound": is_proxy,
            "origin": "self" if r.h3_cell == cell else "neighbour",
            "from_cell": r.h3_cell,
            "simulated": r.source_id in SCENARIO_SOURCE_IDS,
        })

    return {
        "tool": "get_conditions",
        "place": _place_block(place),
        "window": _window_block(w),
        "h3_cell": cell,
        "observations": observations,
        "sources": await _sources_block(conn, cell, w, now),
        "simulated": any(o["simulated"] for o in observations),
    }


TOOLS = {
    "get_risk": get_risk,
    "check_boundaries": check_boundaries,
    "get_conditions": get_conditions,
}


async def run_tool(conn: Any, name: str, raw_args: dict[str, Any],
                   now: datetime | None = None) -> dict[str, Any]:
    """Validate then execute. Validation errors are RESULTS, not exceptions:
    the agent must be able to tell the user "I don't know that place" rather
    than crashing or, worse, guessing.
    """
    if name not in TOOLS:
        return {"tool": name, "error": "unknown_tool",
                "message": f"{name!r} is not a tool. Available: {TOOL_NAMES}"}
    try:
        args = SCHEMAS[name](**raw_args)
    except Exception as e:
        return {"tool": name, "error": "invalid_arguments", "message": str(e)}

    try:
        return await TOOLS[name](conn, args, now=now)
    except UnknownPlace as e:
        return {"tool": name, "error": "unknown_place",
                "message": str(e), "known_places": e.known}
