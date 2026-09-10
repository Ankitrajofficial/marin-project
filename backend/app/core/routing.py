"""Time-dependent A* over a moving hazard field.

THE PROBLEM
-----------
A boat has to get from where it is to somewhere safe. The obvious answer --
shortest line -- is wrong twice over: it ignores that the dangerous water moves
while the boat is travelling, and it ignores that some water is not merely
dangerous but forbidden.

So the search is over (CELL, TIME) states rather than cells. The same cell
reached two hours later is a different proposition, because the storm has moved
in the meantime. A route planned against a single snapshot of the hazard field
is a route planned against a field that will not exist by the time the boat
gets there.

WHAT IS A HARD CONSTRAINT AND WHAT IS A COST
--------------------------------------------
Costs are things an optimiser may trade away. Constraints are not.

  HARD -- edges are REMOVED from the graph, never priced:
    * crossing a maritime boundary (the IMBL). Not configurable off in this
      module. A weighted penalty is something a search can decide to pay, and
      the price of paying this one is a crew in a foreign jail.
    * entering a cell whose hazard probability exceeds the vessel's ceiling.
      This is what stops the router returning a least-bad path dressed as a
      route.
    * entering a restricted zone. Which zone types block is configurable --
      an authority might override a protected area in an emergency; nobody
      overrides a treaty boundary.

  COST -- traded normally:
    * transit time
    * hazard exposure below the ceiling, as a multiplier on time (see ALPHA)

WHAT COUNTS AS WATER
--------------------
A cell is navigable if we have a computed hazard value for it. That works as a
water mask for free: the marine model returns null over land, adapters drop
missing readings, so a cell with wave data is a cell with water in it.

The cost of that trick, stated rather than buried: A COVERAGE GAP IS
INDISTINGUISHABLE FROM LAND. The router will refuse to cross a cell it has no
data for, and will report "no route" where the truth is "not surveyed". That is
the right direction to fail in -- routing a boat through an unknown cell is
worse -- but it means every answer here carries the coverage it was computed
against.

WAITING IS NOT MODELLED
-----------------------
Formally the search is incomplete because of this: with a time-varying field,
sitting still until the storm passes can beat every route available now, and
this search will never find that solution. It is disallowed on purpose --
loitering in open water in a cyclone is not a recall strategy. When no route is
found, the result says whether waiting MIGHT help (i.e. whether the blocking
was hazard, which moves, rather than geography, which does not). It does not
claim to have computed that it would.

WHAT IS NOT CHECKED
-------------------
Under-keel clearance along the route. Draft is checked against the destination
harbour only. Doing it properly needs bathymetry -- and unlike harbour depth,
this is exactly what GEBCO measures, so it is a real gap with a known fix
rather than an unanswerable question. Every result carries
under_keel_checked=False so nothing downstream can imply otherwise.
"""

from __future__ import annotations

import heapq
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.core.grid import centroid, disk, neighbours

log = logging.getLogger(__name__)

METRES_PER_NM = 1852.0
EARTH_RADIUS_M = 6_371_008.8

# ---------------------------------------------------------------------------
# Tunables, documented and reported in every result.

#: How many hours of detour are worth avoiding one hour spent at hazard
#: probability 1.0. Hazard enters the cost as a MULTIPLIER on transit time --
#: dangerous water is "longer" -- so this constant has a statable meaning
#: instead of being an arbitrary weight on incommensurable units.
#: At 4.0, a skipper will accept a 4-hour detour to avoid an hour of certain
#: hazard, and proportionally less for lower probabilities.
ALPHA = 4.0

#: Default ceiling above which a cell may not be entered at all.
DEFAULT_MAX_HAZARD = 0.60

#: Zone types that block passage. IMBL is always blocking and is added
#: unconditionally below -- it is not in this set because it is not optional.
DEFAULT_BLOCKING_ZONES = frozenset({"mpa"})
ALWAYS_BLOCKING_ZONES = frozenset({"imbl"})

#: Search guard. A* over ~1000 cells x ~50 hours should settle in far fewer
#: expansions than this; hitting it means something is wrong, and returning
#: "no route" is better than hanging an API request.
MAX_EXPANSIONS = 400_000


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


@dataclass(frozen=True, slots=True)
class VesselConstraints:
    speed_ms: float
    draft_m: float | None = None
    max_hazard_prob: float = DEFAULT_MAX_HAZARD


@dataclass(slots=True)
class RouteGraph:
    """Everything the search needs, loaded once per request.

    Built from the database rather than queried during the search: an A* that
    round-trips to PostGIS per edge would spend all its time in the driver.
    """

    t0: datetime                                   # hour 0 of the hazard field
    n_hours: int
    #: (cell, hour_index) -> hazard probability. Absence means UNKNOWN, which
    #: is treated as impassable, not as safe.
    hazard: dict[tuple[str, int], float]
    navigable: set[str]
    #: Cells whose centre lies inside a blocking zone.
    blocked_cells: set[str]
    #: Undirected edges whose great-circle segment crosses a blocking zone.
    blocked_edges: set[frozenset[str]]
    #: Cells admitted as goals despite having no marine data -- harbours are
    #: partly land by definition.
    goal_exempt: set[str] = field(default_factory=set)
    #: True when any cell in the field came from scenario data. Propagated onto
    #: every route computed against it, so a simulated route can never be
    #: presented as a real one.
    simulated: bool = False

    def hour_index(self, when: datetime) -> int:
        return int(round((when - self.t0).total_seconds() / 3600.0))

    def hazard_at(self, cell: str, when: datetime) -> float | None:
        """Hazard probability, or None if we do not know.

        None is returned past the end of the forecast horizon too. A route
        whose later legs fall beyond the horizon is a route through water we
        have no information about, and that is not a route.
        """
        idx = self.hour_index(when)
        if idx < 0:
            idx = 0
        if idx >= self.n_hours:
            return None
        return self.hazard.get((cell, idx))


@dataclass(slots=True)
class RouteResult:
    found: bool = False
    #: Ordered H3 cells, start to goal.
    cells: list[str] = field(default_factory=list)
    #: [lon, lat] positions, ready for GeoJSON.
    coordinates: list[list[float]] = field(default_factory=list)
    depart_time: datetime | None = None
    eta: datetime | None = None
    duration_h: float | None = None
    distance_nm: float | None = None
    #: Probability-hours: sum over legs of hazard_prob x hours in that leg.
    #: "One hour at 50%" and "two hours at 25%" are the same exposure.
    hazard_exposure: float | None = None
    max_hazard_on_route: float | None = None
    #: What limited the answer -- reported whether or not a route was found.
    binding_constraints: list[str] = field(default_factory=list)
    blocked_reason: str | None = None
    #: True when the blocking was HAZARD (which moves) rather than geography
    #: (which does not). We do NOT compute whether waiting would actually
    #: work -- only whether it is the kind of obstruction that time changes.
    waiting_might_help: bool = False
    #: Always False. Nothing here checks depth along the route.
    under_keel_checked: bool = False
    alpha: float = ALPHA
    max_hazard_prob: float | None = None
    expansions: int = 0
    simulated: bool = False


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
_RISK_SQL = """
SELECT h3_cell, valid_time, hazard_prob, simulated
  FROM risk_cells
 WHERE valid_time >= %(t0)s AND valid_time < %(t1)s
"""

# One bulk query instead of a PostGIS round trip per edge. Segments are built
# from the caller's arrays and tested against the blocking zone geometries in
# a single pass.
_BLOCKED_EDGES_SQL = """
WITH seg AS (
    SELECT i, ST_SetSRID(ST_MakeLine(ST_MakePoint(lon1, lat1),
                                     ST_MakePoint(lon2, lat2)), 4326) AS g
      FROM unnest(%(idx)s::int[], %(lat1)s::float8[], %(lon1)s::float8[],
                  %(lat2)s::float8[], %(lon2)s::float8[])
        AS t(i, lat1, lon1, lat2, lon2)
)
SELECT DISTINCT seg.i
  FROM seg JOIN hazard_zones z ON ST_Intersects(seg.g, z.geom)
 WHERE z.zone_type = ANY(%(types)s)
"""

_BLOCKED_CELLS_SQL = """
SELECT DISTINCT c.cell
  FROM unnest(%(cells)s::text[], %(lats)s::float8[], %(lons)s::float8[])
       AS c(cell, lat, lon)
  JOIN hazard_zones z
    ON ST_Contains(z.geom, ST_SetSRID(ST_MakePoint(c.lon, c.lat), 4326))
 WHERE z.zone_type = ANY(%(types)s)
"""


async def build_graph(conn: Any, t0: datetime, horizon_h: int = 48,
                      blocking_zones: Iterable[str] = DEFAULT_BLOCKING_ZONES,
                      ) -> RouteGraph:
    """Load the hazard field and precompute every hard constraint."""
    t0 = t0.replace(minute=0, second=0, microsecond=0)
    t1 = t0 + timedelta(hours=horizon_h)
    types = sorted(set(blocking_zones) | ALWAYS_BLOCKING_ZONES)

    hazard: dict[tuple[str, int], float] = {}
    navigable: set[str] = set()
    simulated = False

    async with conn.cursor() as cur:
        await cur.execute(_RISK_SQL, {"t0": t0, "t1": t1})
        for cell, valid_time, prob, sim in await cur.fetchall():
            if prob is None:
                # hazard_prob NULL means NO DATA, not calm. Leaving it out of
                # the map makes the cell impassable, which is the honest
                # reading.
                continue
            idx = int(round((valid_time - t0).total_seconds() / 3600.0))
            if 0 <= idx < horizon_h:
                hazard[(cell, idx)] = float(prob)
                navigable.add(cell)
                simulated = simulated or bool(sim)

        cells = sorted(navigable)
        blocked_cells: set[str] = set()
        blocked_edges: set[frozenset[str]] = set()

        if cells:
            coords = {c: centroid(c) for c in cells}
            await cur.execute(_BLOCKED_CELLS_SQL, {
                "cells": cells,
                "lats": [coords[c][0] for c in cells],
                "lons": [coords[c][1] for c in cells],
                "types": types,
            })
            blocked_cells = {r[0] for r in await cur.fetchall()}

            # Undirected edge list, deduped so each segment is tested once.
            pairs: list[tuple[str, str]] = []
            seen: set[frozenset[str]] = set()
            for c in cells:
                for n in neighbours(c, 1):
                    if n in navigable:
                        key = frozenset((c, n))
                        if key not in seen:
                            seen.add(key)
                            pairs.append((c, n))
            if pairs:
                await cur.execute(_BLOCKED_EDGES_SQL, {
                    "idx": list(range(len(pairs))),
                    "lat1": [coords[a][0] for a, _ in pairs],
                    "lon1": [coords[a][1] for a, _ in pairs],
                    "lat2": [coords[b][0] for _, b in pairs],
                    "lon2": [coords[b][1] for _, b in pairs],
                    "types": types,
                })
                for (i,) in await cur.fetchall():
                    blocked_edges.add(frozenset(pairs[i]))

    g = RouteGraph(t0=t0, n_hours=horizon_h, hazard=hazard, navigable=navigable,
                   blocked_cells=blocked_cells, blocked_edges=blocked_edges,
                   simulated=simulated)
    log.info("route graph: %d navigable cells, %d blocked cells, %d blocked "
             "edges, %d hours, simulated=%s", len(navigable), len(blocked_cells),
             len(blocked_edges), horizon_h, simulated)
    return g


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------
def find_route(graph: RouteGraph, start: str, goal: str, depart: datetime,
               vessel: VesselConstraints) -> RouteResult:
    """Time-dependent A* from `start` to `goal` departing at `depart`.

    State is (cell, hour_index): the same cell at a different hour is a
    different node, because the hazard there is different.

    The heuristic is great-circle distance to the goal divided by the vessel's
    speed. That is admissible -- it can never overestimate -- because the true
    cost of any remaining path is at least its transit time, and the hazard
    multiplier is >= 1. Admissibility is what makes the first path A* pops the
    optimal one.
    """
    result = RouteResult(depart_time=depart, alpha=ALPHA,
                         max_hazard_prob=vessel.max_hazard_prob,
                         simulated=graph.simulated)

    if start == goal:
        lat, lon = centroid(start)
        return RouteResult(
            found=True, cells=[start], coordinates=[[lon, lat]],
            depart_time=depart, eta=depart, duration_h=0.0, distance_nm=0.0,
            hazard_exposure=0.0, max_hazard_on_route=graph.hazard_at(start, depart) or 0.0,
            binding_constraints=["already at destination"],
            alpha=ALPHA, max_hazard_prob=vessel.max_hazard_prob,
            simulated=graph.simulated,
        )

    if start not in graph.navigable:
        result.blocked_reason = (
            "the vessel's own cell has no computed hazard field, so no route "
            "can be planned from it. This is missing coverage, not a blocked sea."
        )
        result.binding_constraints = ["coverage"]
        return result

    goal_ok = goal in graph.navigable or goal in graph.goal_exempt
    if not goal_ok:
        result.blocked_reason = (
            "the destination cell has no computed hazard field and is not a "
            "known harbour cell."
        )
        result.binding_constraints = ["coverage"]
        return result

    goal_lat, goal_lon = centroid(goal)
    speed = max(0.1, vessel.speed_ms)

    def heuristic(cell: str) -> float:
        lat, lon = centroid(cell)
        return haversine_m(lat, lon, goal_lat, goal_lon) / speed / 3600.0

    # (priority, cost_so_far, cell, elapsed_h, exposure, distance_m, max_p)
    start_state = (start, 0)
    open_heap: list[tuple[float, float, str, float, float, float, float]] = [
        (heuristic(start), 0.0, start, 0.0, 0.0, 0.0, 0.0)
    ]
    best: dict[tuple[str, int], float] = {start_state: 0.0}
    parent: dict[tuple[str, int], tuple[str, int] | None] = {start_state: None}

    # Why edges were rejected, so a failure can say WHICH constraint bound it
    # instead of just "no route".
    rejected = {"hazard": 0, "zone": 0, "imbl": 0, "unknown": 0, "horizon": 0}
    expansions = 0

    while open_heap:
        _, cost, cell, elapsed, exposure, dist_m, max_p = heapq.heappop(open_heap)
        idx = graph.hour_index(depart + timedelta(hours=elapsed))
        idx = max(0, min(idx, graph.n_hours - 1))
        state = (cell, idx)
        if cost > best.get(state, math.inf):
            continue

        if cell == goal:
            cells: list[str] = []
            s: tuple[str, int] | None = state
            while s is not None:
                cells.append(s[0])
                s = parent.get(s)
            cells.reverse()
            coords = [[centroid(c)[1], centroid(c)[0]] for c in cells]

            binding: list[str] = []
            if rejected["imbl"]:
                binding.append("maritime boundary (route diverted around it)")
            if rejected["zone"]:
                binding.append("restricted zone (route diverted around it)")
            if rejected["hazard"]:
                binding.append("hazard ceiling (route diverted around it)")
            if rejected["unknown"] or rejected["horizon"]:
                binding.append("coverage (unmapped or beyond forecast horizon)")
            if not binding:
                binding.append("transit time only")

            result.found = True
            result.cells = cells
            result.coordinates = coords
            result.eta = depart + timedelta(hours=elapsed)
            result.duration_h = round(elapsed, 3)
            result.distance_nm = round(dist_m / METRES_PER_NM, 2)
            result.hazard_exposure = round(exposure, 4)
            result.max_hazard_on_route = round(max_p, 4)
            result.binding_constraints = binding
            result.expansions = expansions
            return result

        expansions += 1
        if expansions > MAX_EXPANSIONS:
            result.blocked_reason = (
                f"search exceeded {MAX_EXPANSIONS} expansions without reaching "
                f"the destination; reporting no route rather than an "
                f"unbounded wait."
            )
            result.binding_constraints = ["search limit"]
            result.expansions = expansions
            return result

        lat_u, lon_u = centroid(cell)
        for nb in neighbours(cell, 1):
            if nb not in graph.navigable and nb != goal:
                rejected["unknown"] += 1
                continue
            if nb in graph.blocked_cells and nb != goal:
                rejected["zone"] += 1
                continue
            edge = frozenset((cell, nb))
            if edge in graph.blocked_edges:
                # Crossing a treaty boundary or a restricted zone edge. Removed,
                # not priced.
                rejected["imbl"] += 1
                continue

            lat_v, lon_v = centroid(nb)
            leg_m = haversine_m(lat_u, lon_u, lat_v, lon_v)
            leg_h = leg_m / speed / 3600.0
            arrive = depart + timedelta(hours=elapsed + leg_h)

            if graph.hour_index(arrive) >= graph.n_hours:
                rejected["horizon"] += 1
                continue

            p = graph.hazard_at(nb, arrive)
            if p is None:
                if nb == goal:
                    p = 0.0        # harbour cell, no field; arrival is the end
                else:
                    rejected["unknown"] += 1
                    continue
            # HARD: never enter water above the ceiling. Not a penalty.
            if p > vessel.max_hazard_prob and nb != goal:
                rejected["hazard"] += 1
                continue

            new_cost = cost + leg_h * (1.0 + ALPHA * p)
            n_idx = max(0, min(graph.hour_index(arrive), graph.n_hours - 1))
            n_state = (nb, n_idx)
            if new_cost >= best.get(n_state, math.inf):
                continue

            best[n_state] = new_cost
            parent[n_state] = state
            heapq.heappush(open_heap, (
                new_cost + heuristic(nb), new_cost, nb,
                elapsed + leg_h, exposure + p * leg_h,
                dist_m + leg_m, max(max_p, p),
            ))

    # ---- exhausted without reaching the goal ----------------------------
    reasons = []
    if rejected["imbl"]:
        reasons.append(f"{rejected['imbl']} edge(s) blocked by a maritime "
                       f"boundary or restricted zone")
    if rejected["hazard"]:
        reasons.append(f"{rejected['hazard']} edge(s) blocked by the hazard "
                       f"ceiling of {vessel.max_hazard_prob:.0%}")
    if rejected["unknown"]:
        reasons.append(f"{rejected['unknown']} edge(s) led into cells with no "
                       f"hazard data (unmapped, not necessarily land)")
    if rejected["horizon"]:
        reasons.append(f"{rejected['horizon']} edge(s) would arrive beyond the "
                       f"forecast horizon")

    result.blocked_reason = ("No safe route exists. " + "; ".join(reasons) + "."
                             if reasons else
                             "No safe route exists: the destination is not "
                             "reachable through navigable water.")
    result.binding_constraints = [k for k, v in rejected.items() if v]
    # Hazard moves; geography does not. If the obstruction was hazard, waiting
    # is the KIND of thing that could change the answer -- but this is not a
    # claim that it would, and nothing here searched for such a solution.
    result.waiting_might_help = rejected["hazard"] > 0
    result.expansions = expansions
    return result
