"""Vessel recall triage: who has to turn back first.

THE QUESTION THIS ANSWERS
-------------------------
A cyclone is coming. Two hundred boats are at sea. A coastal officer has one
radio and a few hours. WHO DO THEY CALL FIRST?

Not the boat in the worst weather -- that boat may be an hour from shelter and
perfectly able to save itself. The boat that needs calling first is the one
with the least SLACK: the smallest gap between how long it needs to reach
safety and how long it has before the hazard arrives.

    time_to_harbour = (distance to nearest suitable harbour x detour) / speed
    time_to_hazard  = when hazard_prob at the vessel's cell first crosses
                      the threshold
    margin          = time_to_hazard - time_to_harbour

Rank ascending. A negative margin means the boat cannot make it and needs a
different plan -- a closer harbour, a course that avoids the worst cell, or a
rescue asset. That is the number this module exists to surface.

THREE WAYS THIS COULD LIE, AND WHAT IS DONE ABOUT EACH
------------------------------------------------------
1. STRAIGHT-LINE DISTANCE IS OPTIMISTIC. A real track goes around headlands
   and shoals. An optimistic time_to_harbour INFLATES the margin, which is the
   dangerous direction -- it makes a boat look safer than it is. Until
   core/routing.py exists, a documented detour factor is applied and every
   result is labelled `distance_is_straight_line`. The factor is not a fudge;
   it is an admission, and it is visible in the output.

2. A STALE POSITION IS NOT A KNOWN POSITION. A fix twenty minutes old on a
   boat making 5 m/s could be anywhere in a 3 NM circle. That radius is
   computable, so it is computed and reported rather than merely flagged.

3. MISSING DATA MUST NEVER RANK AS SAFE. A vessel whose cell has no computed
   risk, or that has no depth-compatible harbour, does NOT get a large margin
   and sink to the bottom of the list. It goes in a separate `cannot_assess`
   group that a human has to look at. Sorting an unknown as if it were good
   news is exactly how a boat gets left out.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import SCENARIO_SOURCE_IDS

log = logging.getLogger(__name__)

METRES_PER_NM = 1852.0
EARTH_RADIUS_M = 6_371_008.8

# ---------------------------------------------------------------------------
# Tunables, all documented, all reported in the output.

#: Multiplier on great-circle distance, standing in for a real route until
#: core/routing.py lands. 1.25 is a conservative coastal-passage rule of thumb.
#: Raising it makes every margin smaller -- i.e. more cautious.
DETOUR_FACTOR = 1.25

#: A position older than this is not trusted as current.
STALE_POSITION_S = 30 * 60

#: Positions older than this are not used at all -- a two-hour-old fix on a
#: moving boat is a different boat's problem.
MAX_POSITION_AGE_S = 6 * 3600

#: Recent SOG samples needed before we believe an observed transit speed
#: instead of the class default.
MIN_SPEED_SAMPLES = 3
#: Below this the vessel is drifting or working, not transiting, so the sample
#: says nothing about how fast it can run for shelter.
UNDERWAY_MIN_SOG_MS = 0.5
#: Floor on assumed speed. Without it, a fleet of stationary boats divides by
#: nearly zero and every margin becomes -infinity.
MIN_ASSUMED_SPEED_MS = 1.0

#: Default hazard probability at which a cell counts as dangerous.
DEFAULT_THRESHOLD = 0.30

CANNOT_ASSESS = "cannot_assess"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass(slots=True)
class HarbourChoice:
    harbour_id: str
    name: str
    lat: float
    lon: float
    harbour_type: str | None
    distance_nm: float
    #: 'osm' means a real tagged depth. 'unknown' means OSM had none -- which
    #: is EVERY harbour in our AOI today. Never read as "deep enough".
    depth_source: str
    depth_m: float | None
    draft_ok: bool | None        # None = unverifiable, NOT True
    #: Which dataset this destination came from, so a trace can say whether
    #: it chose an INCOIS landing centre or an OSM harbour.
    source_id: str | None = None
    #: Set when the source is a frozen snapshot rather than a live feed. The
    #: INCOIS landing-centre layer is fixed at 2024-04-27; locations barely
    #: move, but the age is reported rather than presented as current.
    snapshot_date: str | None = None
    data_age_days: float | None = None


@dataclass(slots=True)
class RecallEntry:
    mmsi: str
    name: str | None
    vessel_class: str | None
    lat: float
    lon: float
    h3_cell: str

    position_time: datetime
    position_age_s: float
    position_is_stale: bool
    #: age x speed. How far the boat could be from where we last saw it.
    position_uncertainty_nm: float

    speed_ms: float
    speed_source: str            # 'observed_p75' | 'class_default'
    speed_samples: int

    harbour: HarbourChoice | None
    time_to_harbour_h: float | None
    #: True when time_to_harbour came from a real routed path; False when it
    #: fell back to great-circle x DETOUR_FACTOR. Per-entry, because after
    #: routing exists the caveat belongs on the specific vessels that still
    #: need it, not on the whole response.
    time_is_routed: bool = False
    route_cells: list[str] = field(default_factory=list)
    route_coordinates: list[list[float]] = field(default_factory=list)
    route_distance_nm: float | None = None
    route_exposure: float | None = None
    route_max_hazard: float | None = None
    route_blocked_reason: str | None = None
    route_waiting_might_help: bool = False
    #: Nothing checks depth along the route. Surfaced, never assumed.
    under_keel_checked: bool = False

    time_to_hazard_h: float | None = None
    hazard_time: datetime | None = None
    hazard_prob_at_crossing: float | None = None
    hazard_driver: str | None = None
    hazard_data_age_s: float | None = None

    margin_h: float | None = None
    status: str = "ranked"       # 'ranked' | 'cannot_assess'
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    simulated: bool = False
    draft_m: float | None = None


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------
def rank_harbours(lat: float, lon: float, draft_m: float | None,
                  harbours: list[dict[str, Any]], limit: int = 4
                  ) -> list[HarbourChoice]:
    """Candidate harbours, nearest first.

    More than one because the nearest harbour may have no ROUTE to it -- the
    storm, a boundary or the coastline can be in the way -- and a vessel that
    cannot reach its closest shelter is not a vessel with no shelter.
    """
    out: list[HarbourChoice] = []
    for h in harbours:
        if h.get("harbour_type") == "shipyard":
            continue
        depth_m = h.get("depth_m")
        if depth_m is not None and draft_m is not None:
            if depth_m <= draft_m:
                continue
            draft_ok: bool | None = True
        else:
            draft_ok = None
        meta = h.get("meta") or {}
        snapshot = meta.get("snapshot_date")
        # Age from the snapshot date when the source is frozen, otherwise from
        # when we fetched it. A snapshot's real age is its content date, not
        # the moment we downloaded it again.
        age_days = None
        if snapshot:
            try:
                snap = datetime.fromisoformat(snapshot).replace(tzinfo=timezone.utc)
                age_days = round((datetime.now(timezone.utc) - snap).days, 1)
            except ValueError:
                pass
        elif h.get("fetched_at"):
            age_days = round(
                (datetime.now(timezone.utc) - h["fetched_at"]).total_seconds()
                / 86400.0, 2)

        out.append(HarbourChoice(
            harbour_id=h["harbour_id"], name=h["name"], lat=h["lat"], lon=h["lon"],
            harbour_type=h.get("harbour_type"),
            distance_nm=haversine_m(lat, lon, h["lat"], h["lon"]) / METRES_PER_NM,
            depth_source=h.get("depth_source", "unknown"), depth_m=depth_m,
            draft_ok=draft_ok, source_id=h.get("source_id"),
            snapshot_date=snapshot, data_age_days=age_days,
        ))
    out.sort(key=lambda c: c.distance_nm)
    return out[:limit]


def effective_speed(sog_samples: list[float], class_default: float | None
                    ) -> tuple[float, str, int]:
    """How fast this boat can realistically run for shelter.

    Prefers what it has actually been doing (75th percentile of underway SOG)
    over a class default, because a laden trawler is not a catalogue entry.
    Falls back to the class default, which is deliberately at the slow end --
    a slower assumption means a smaller margin and a HIGHER recall priority,
    so the guess errs toward calling the boat in early.
    """
    underway = sorted(s for s in sog_samples if s and s > UNDERWAY_MIN_SOG_MS)
    if len(underway) >= MIN_SPEED_SAMPLES:
        idx = max(0, min(len(underway) - 1, int(round(0.75 * (len(underway) - 1)))))
        return max(underway[idx], MIN_ASSUMED_SPEED_MS), "observed_p75", len(underway)
    return (max(class_default or 3.1, MIN_ASSUMED_SPEED_MS),
            "class_default", len(underway))


def first_hazard_crossing(steps: list[dict[str, Any]], threshold: float
                          ) -> tuple[datetime | None, float | None, str | None]:
    """First time the cell's hazard probability reaches the threshold.

    Returns (time, probability, dominant driver). None means it never crosses
    WITHIN THE COMPUTED HORIZON -- which is not the same as "it never will",
    and the caller reports the horizon alongside it.
    """
    for s in steps:
        p = s.get("hazard_prob")
        if p is not None and p >= threshold:
            drivers = (s.get("drivers") or {}).get("contributions") or {}
            top = max(drivers, key=drivers.get) if drivers else None
            return s["valid_time"], p, top
    return None, None, None


def assess(vessel: dict[str, Any], harbours: list[dict[str, Any]],
           risk_steps: list[dict[str, Any]], sog_samples: list[float],
           now: datetime, threshold: float = DEFAULT_THRESHOLD,
           horizon_end: datetime | None = None,
           route_fn: Any = None) -> RecallEntry:
    """Everything for one vessel. Pure: `now` is passed in, nothing is fetched.

    `route_fn(from_cell, to_cell, speed_ms, draft_m)` -> RouteResult | None is
    injected rather than imported so this function stays testable and free of
    database access. When it is supplied, time_to_harbour is the REAL routed
    time and the straight-line caveat no longer applies to this vessel.
    """
    flags: list[str] = []
    reasons: list[str] = []

    pos_time: datetime = vessel["ts"]
    age_s = (now - pos_time).total_seconds()
    stale = age_s > STALE_POSITION_S

    speed, speed_source, n_samples = effective_speed(
        sog_samples, vessel.get("cruise_speed"))
    if speed_source == "class_default":
        flags.append("speed_assumed_from_class")

    # A stale fix is not a known position. Report how wrong it could be.
    uncertainty_nm = max(0.0, age_s) * speed / METRES_PER_NM
    if stale:
        flags.append("stale_position")
        reasons.append(
            f"last fix {age_s / 60:.0f} min old; at {speed:.1f} m/s the vessel "
            f"could be up to {uncertainty_nm:.1f} NM from this position"
        )

    candidates = rank_harbours(vessel["lat"], vessel["lon"],
                               vessel.get("draft_m"), harbours)
    harbour: HarbourChoice | None = candidates[0] if candidates else None
    tth_h = None
    routed = False
    route_cells: list[str] = []
    route_coords: list[list[float]] = []
    route_dist = route_exp = route_maxp = None
    route_blocked: str | None = None
    route_waiting = False

    if not candidates:
        reasons.append("no candidate harbour found")
    else:
        if route_fn is not None:
            # Try the nearest harbours in order: the closest shelter may have
            # no route to it, and that is not the same as having no shelter.
            for cand in candidates:
                r = route_fn(vessel["h3_cell"], cand, speed, vessel.get("draft_m"))
                if r is None:
                    continue
                if r.found:
                    harbour = cand
                    tth_h = r.duration_h
                    routed = True
                    route_cells = r.cells
                    route_coords = r.coordinates
                    route_dist = r.distance_nm
                    route_exp = r.hazard_exposure
                    route_maxp = r.max_hazard_on_route
                    break
                # Remember why the NEAREST one failed, for the report.
                if route_blocked is None:
                    route_blocked = f"{cand.name}: {r.blocked_reason}"
                    route_waiting = r.waiting_might_help
        if not routed:
            # Straight-line fallback. OPTIMISTIC -- a real track is longer --
            # so the vessel is flagged rather than the whole response.
            harbour = candidates[0]
            tth_h = (harbour.distance_nm * DETOUR_FACTOR * METRES_PER_NM) / speed / 3600.0
            if route_fn is not None:
                flags.append("no_route_found")
                reasons.append(
                    "no safe route to any candidate harbour; the time below is "
                    "a straight-line estimate and is OPTIMISTIC"
                    + (f" ({route_blocked})" if route_blocked else "")
                )
            else:
                flags.append("straight_line_estimate")

    if harbour and harbour.draft_ok is None:
        flags.append("draft_unverified")
        if vessel.get("draft_m") is None:
            reasons.append("vessel draught unknown (no AIS static message), so "
                           "harbour suitability is unverified")
        else:
            reasons.append(f"{harbour.name} has no charted depth in "
                           f"OpenStreetMap, so it is unverified for a "
                           f"{vessel['draft_m']} m draught")

    hazard_time, hazard_p, driver = first_hazard_crossing(risk_steps, threshold)
    hazard_age_s = None
    if risk_steps:
        issued = [s.get("issued_time") for s in risk_steps if s.get("issued_time")]
        if issued:
            hazard_age_s = (now - max(issued)).total_seconds()

    simulated = bool(vessel.get("simulated")) or any(
        s.get("simulated") for s in risk_steps
    ) or any(s.get("source_id") in SCENARIO_SOURCE_IDS for s in risk_steps)

    # ---- can we assess at all? -----------------------------------------
    status = "ranked"
    if not risk_steps:
        status = CANNOT_ASSESS
        reasons.append("no hazard field computed for this vessel's cell — "
                       "unknown, not safe")
        flags.append("no_hazard_data")
    if harbour is None:
        status = CANNOT_ASSESS
        flags.append("no_harbour")
    if age_s > MAX_POSITION_AGE_S:
        status = CANNOT_ASSESS
        reasons.append(f"position {age_s / 3600:.1f} h old — too stale to place "
                       f"this vessel at all")
        flags.append("position_too_old")

    time_to_hazard_h = None
    margin_h = None
    if hazard_time is not None:
        time_to_hazard_h = (hazard_time - now).total_seconds() / 3600.0
        if tth_h is not None:
            margin_h = time_to_hazard_h - tth_h
    elif status == "ranked" and risk_steps:
        # No crossing within the horizon. Ranked, but explicitly bounded --
        # "no hazard in the next 40 h" is a real answer; "no hazard ever" is
        # not one we can give.
        end = horizon_end or risk_steps[-1]["valid_time"]
        reasons.append(
            f"hazard probability stays below {threshold:.0%} through the "
            f"computed horizon (to {end.isoformat()})"
        )
        flags.append("no_crossing_in_horizon")

    return RecallEntry(
        mmsi=vessel["mmsi"], name=vessel.get("name"),
        vessel_class=vessel.get("vessel_class"),
        lat=vessel["lat"], lon=vessel["lon"], h3_cell=vessel["h3_cell"],
        position_time=pos_time, position_age_s=age_s, position_is_stale=stale,
        position_uncertainty_nm=uncertainty_nm,
        speed_ms=speed, speed_source=speed_source, speed_samples=n_samples,
        harbour=harbour, time_to_harbour_h=tth_h,
        time_is_routed=routed, route_cells=route_cells,
        route_coordinates=route_coords, route_distance_nm=route_dist,
        route_exposure=route_exp, route_max_hazard=route_maxp,
        route_blocked_reason=route_blocked,
        route_waiting_might_help=route_waiting,
        under_keel_checked=False,
        time_to_hazard_h=time_to_hazard_h, hazard_time=hazard_time,
        hazard_prob_at_crossing=hazard_p, hazard_driver=driver,
        hazard_data_age_s=hazard_age_s,
        margin_h=margin_h, status=status, reasons=reasons, flags=flags,
        simulated=simulated, draft_m=vessel.get("draft_m"),
    )


def rank(entries: list[RecallEntry]) -> tuple[list[RecallEntry], list[RecallEntry]]:
    """Split into a ranked list and a cannot-assess list.

    Ranking order, smallest slack first:
      1. a computable margin, ascending (negative = cannot make it)
      2. no hazard crossing in the horizon, by time-to-harbour
    `cannot_assess` is returned SEPARATELY and never merged. A vessel we could
    not evaluate must land in front of a human, not at the bottom of a list
    someone reads the top of.
    """
    ranked = [e for e in entries if e.status == "ranked"]
    unknown = [e for e in entries if e.status == CANNOT_ASSESS]

    def key(e: RecallEntry) -> tuple[int, float]:
        if e.margin_h is not None:
            return (0, e.margin_h)
        return (1, e.time_to_harbour_h if e.time_to_harbour_h is not None else 1e9)

    ranked.sort(key=key)
    unknown.sort(key=lambda e: e.position_age_s)
    return ranked, unknown


# ===========================================================================
# Reading the inputs
# ===========================================================================
# Takes a connection rather than opening one -- core/ does not own connection
# lifecycle. Same rule as core/grid.fetch_observations.

_LATEST_POS = """
SELECT DISTINCT ON (vp.mmsi)
       vp.mmsi, vp.ts, vp.lat, vp.lon, vp.h3_cell,
       v.name, v.vessel_class, v.cruise_speed, v.draft_m
  FROM vessel_positions vp
  LEFT JOIN vessels v ON v.mmsi = vp.mmsi
 WHERE vp.ts >= %(since)s
   {bbox}
 ORDER BY vp.mmsi, vp.ts DESC
"""

_SOG_SAMPLES = """
SELECT mmsi, array_agg(sog ORDER BY ts DESC)
  FROM vessel_positions
 WHERE ts >= %(since)s AND sog IS NOT NULL
 GROUP BY mmsi
"""

_HARBOURS = """
SELECT harbour_id, name, lat, lon, depth_m, depth_source, harbour_type,
       source_id, fetched_at, meta
  FROM harbours
"""

_RISK = """
SELECT h3_cell, valid_time, hazard_prob, uncertainty, simulated, drivers
  FROM risk_cells
 WHERE h3_cell = ANY(%(cells)s) AND valid_time >= %(now)s
 ORDER BY h3_cell, valid_time
"""

# Data age of the hazard side: risk_cells stores no issued_time, so it comes
# from the observations that produced the cell. Neighbour cells are included
# because core/risk.py borrows across one ring.
_OBS_AGE = """
SELECT h3_cell, max(issued_time)
  FROM observations
 WHERE h3_cell = ANY(%(cells)s)
 GROUP BY h3_cell
"""


async def gather(conn: Any, now: datetime,
                 bbox: tuple[float, float, float, float] | None = None,
                 lookback_h: float = 24.0) -> dict[str, Any]:
    """Fetch everything assess() needs, in four queries rather than per-vessel."""
    from app.core.grid import disk

    since = now - timedelta(hours=lookback_h)
    params: dict[str, Any] = {"since": since}
    bbox_sql = ""
    if bbox:
        w, s, e, n = bbox
        bbox_sql = ("AND ST_Intersects(vp.geom, "
                    "ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326))")
        params |= {"w": w, "s": s, "e": e, "n": n}

    async with conn.cursor() as cur:
        await cur.execute(_LATEST_POS.format(bbox=bbox_sql), params)
        cols = ("mmsi", "ts", "lat", "lon", "h3_cell", "name", "vessel_class",
                "cruise_speed", "draft_m")
        vessels = [dict(zip(cols, r)) for r in await cur.fetchall()]

        await cur.execute(_SOG_SAMPLES, {"since": since})
        sog = {m: [float(x) for x in (arr or []) if x is not None]
               for m, arr in await cur.fetchall()}

        await cur.execute(_HARBOURS)
        hcols = ("harbour_id", "name", "lat", "lon", "depth_m", "depth_source",
                 "harbour_type", "source_id", "fetched_at", "meta")
        harbours = [dict(zip(hcols, r)) for r in await cur.fetchall()]

        cells = sorted({v["h3_cell"] for v in vessels if v["h3_cell"]})
        risk: dict[str, list[dict[str, Any]]] = {}
        ages: dict[str, datetime] = {}
        if cells:
            await cur.execute(_RISK, {"cells": cells, "now": now})
            for h3_cell, vt, hp, unc, sim, drivers in await cur.fetchall():
                risk.setdefault(h3_cell, []).append({
                    "valid_time": vt, "hazard_prob": hp, "uncertainty": unc,
                    "simulated": sim, "drivers": drivers or {},
                })
            neigh = sorted({c for cell in cells for c in disk(cell, 1)})
            await cur.execute(_OBS_AGE, {"cells": neigh})
            per_cell = dict(await cur.fetchall())
            for cell in cells:
                candidates = [per_cell[c] for c in disk(cell, 1)
                              if per_cell.get(c)]
                if candidates:
                    ages[cell] = max(candidates)

    for cell, steps in risk.items():
        if cell in ages:
            for s in steps:
                s["issued_time"] = ages[cell]

    return {"vessels": vessels, "sog": sog, "harbours": harbours, "risk": risk}


def build(inputs: dict[str, Any], now: datetime,
          threshold: float = DEFAULT_THRESHOLD, route_fn: Any = None
          ) -> tuple[list[RecallEntry], list[RecallEntry]]:
    entries = [
        assess(v, inputs["harbours"], inputs["risk"].get(v["h3_cell"], []),
               inputs["sog"].get(v["mmsi"], []), now, threshold,
               route_fn=route_fn)
        for v in inputs["vessels"] if v.get("h3_cell")
    ]
    return rank(entries)
