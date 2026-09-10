"""Scenario lifecycle: activate, clear, status.

THE DANGEROUS MODULE. It is the only thing in ORCA that writes synthetic rows
into the same tables as real data, so every rule it must not break is restated
where it is enforced rather than assumed.

MASKING -- WHY MOVE-ASIDE AND NOT ANYTHING ELSE
-----------------------------------------------
A scenario row and a real row for the same (cell, variable, valid_time) have
different source_ids, so nothing stops them coexisting -- and core/risk.py
would read both and fuse them into a reliability-weighted blend of a calm sea
and a cyclone. A half-simulated hazard field is a forecast of nothing.

Deleting the real rows would make clearing impossible. Filtering them on read
would mean editing core/, which must stay untouched. So overlapping real rows
are MOVED into observations_masked and moved back on clear. core/ keeps
querying `observations` exactly as it always has, and inside the scenario it
simply finds only scenario rows.

The mask is scoped to the EXACT (cell, valid_time) keys the scenario writes and
only the variables it emits. Cells the storm never reaches keep their real
data, real sea-surface temperature is never touched, and there is no bare
DELETE anywhere in this file.

NOT REACHABLE FROM INGEST
-------------------------
Nothing in jobs/ingest_*.py imports this package. The only entry points are
jobs/scenario.py and the /api/scenario routes, both of which require an
explicit scenario name.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.base import (
    IssuedTimeKind,
    Observation,
    Variable,
    write_observations,
)
from app.config import SCENARIO_SOURCE_IDS
from app.core.grid import cell_for, centroid, coverage_cells, disk
from app.scenarios.cyclone import SCENARIOS, CycloneSpec, eye_at, haversine_m, waves_from_wind, wind_at
from app.scenarios.fleet import generate as generate_fleet

log = logging.getLogger(__name__)

SCENARIO_SOURCE = "scenario_sim"
assert SCENARIO_SOURCE in SCENARIO_SOURCE_IDS, (
    "the scenario writer's source_id must be in config.SCENARIO_SOURCE_IDS, "
    "or nothing downstream will flag simulated"
)

#: Domain the storm may touch. Bay of Bengal and the Tamil Nadu coast.
SCENARIO_BBOX = (7.5, 78.0, 13.9, 82.5)      # south, west, north, east
#: Generous, because the domain is large; the count is reported on activation.
MAX_SCENARIO_CELLS = 2500

HOURS = 25                                   # 0..24 inclusive
FLEET_SIZE = 50

#: The variables a cyclone replaces. SST and currents are NOT emitted and
#: therefore NOT masked -- the real ones survive underneath the storm.
EMITTED = (Variable.WIND_SPEED, Variable.WIND_DIRECTION,
           Variable.WAVE_HEIGHT, Variable.WAVE_PERIOD, Variable.WAVE_DIRECTION)


# ---------------------------------------------------------------------------
# Field generation
# ---------------------------------------------------------------------------
def build_field(spec: CycloneSpec, start: datetime,
                bbox: tuple[float, float, float, float] = SCENARIO_BBOX,
                hours: int = HOURS) -> list[Observation]:
    """Cyclone field as Observations.

    Goes through the SAME Observation contract as every real adapter: canonical
    units, physical ranges, timezone-aware times, no NaN. A scenario that could
    inject values a real adapter could not would be a hole in the contract.

    Cells outside the vortex at a given hour emit NOTHING. Emitting a calm
    value there would mask real data with a fabricated zero; leaving it absent
    means the real observation stays and the storm has a boundary instead of a
    fabricated calm.
    """
    s, w, n, e = bbox
    cells = coverage_cells(south=s, west=w, north=n, east=e,
                           max_cells=MAX_SCENARIO_CELLS)
    obs: list[Observation] = []

    for cell in cells:
        lat, lon = centroid(cell)
        for h in range(hours):
            valid = start + timedelta(hours=h)
            wind_ms, wind_from = wind_at(spec, lat, lon, float(h))
            if wind_ms <= 0.5:
                continue                      # outside the vortex: leave reality alone
            hs, tp = waves_from_wind(wind_ms)

            common = dict(
                valid_time=valid, issued_time=start,
                # A scenario has a genuine issue time: the moment it was
                # generated. Not a fetch proxy -- nothing was fetched.
                issued_time_kind=IssuedTimeKind.MODEL_RUN,
                lat=lat, lon=lon, source_id=SCENARIO_SOURCE, confidence=0.9,
            )
            obs.append(Observation(variable=Variable.WIND_SPEED,
                                   value=round(wind_ms, 2), unit="m/s", **common))
            obs.append(Observation(variable=Variable.WIND_DIRECTION,
                                   value=round(wind_from, 1), unit="deg", **common))
            if hs > 0.05:
                obs.append(Observation(variable=Variable.WAVE_HEIGHT,
                                       value=round(hs, 2), unit="m", **common))
                obs.append(Observation(variable=Variable.WAVE_PERIOD,
                                       value=round(tp, 2), unit="s", **common))
                # Waves run with the wind in this parameterisation.
                obs.append(Observation(variable=Variable.WAVE_DIRECTION,
                                       value=round(wind_from, 1), unit="deg", **common))
    return obs


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------
# Move-aside, scoped to the exact keys the scenario is about to write.
# A data-modifying CTE so the DELETE and the INSERT are one statement and
# cannot half-happen.
_MASK = """
WITH keys AS (
    SELECT * FROM unnest(%(cells)s::text[], %(times)s::timestamptz[])
        AS t(h3_cell, valid_time)
),
moved AS (
    DELETE FROM observations o
     USING keys k
     WHERE o.h3_cell = k.h3_cell
       AND o.valid_time = k.valid_time
       AND o.variable = ANY(%(vars)s)
       AND o.source_id <> %(scenario_source)s
    RETURNING o.valid_time, o.issued_time, o.issued_time_kind, o.h3_cell,
              o.variable, o.value, o.unit, o.source_id, o.confidence, o.geom
)
INSERT INTO observations_masked
    (scenario_id, valid_time, issued_time, issued_time_kind, h3_cell, variable,
     value, unit, source_id, confidence, geom)
SELECT %(sid)s, valid_time, issued_time, issued_time_kind, h3_cell, variable,
       value, unit, source_id, confidence, geom
  FROM moved
"""

_UNMASK = """
WITH back AS (
    DELETE FROM observations_masked WHERE scenario_id = %(sid)s
    RETURNING valid_time, issued_time, issued_time_kind, h3_cell, variable,
              value, unit, source_id, confidence, geom
)
INSERT INTO observations
    (valid_time, issued_time, issued_time_kind, h3_cell, variable, value, unit,
     source_id, confidence, geom)
SELECT valid_time, issued_time, issued_time_kind, h3_cell, variable, value,
       unit, source_id, confidence, geom
  FROM back
ON CONFLICT (source_id, h3_cell, variable, valid_time) DO NOTHING
"""

_VESSEL = """
INSERT INTO vessels (mmsi, name, vessel_class, cruise_speed, draft_m)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (mmsi) DO UPDATE SET
    name = EXCLUDED.name, vessel_class = EXCLUDED.vessel_class,
    cruise_speed = EXCLUDED.cruise_speed, draft_m = EXCLUDED.draft_m
"""
_VPOS = """
INSERT INTO vessel_positions (ts, mmsi, lat, lon, sog, cog, h3_cell)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (mmsi, ts) DO UPDATE SET
    lat = EXCLUDED.lat, lon = EXCLUDED.lon, sog = EXCLUDED.sog,
    cog = EXCLUDED.cog, h3_cell = EXCLUDED.h3_cell
"""


async def active_run(conn: Any) -> dict[str, Any] | None:
    async with conn.cursor() as cur:
        await cur.execute("""
            SELECT scenario_id, name, activated_at, params, counts, vessel_ids
              FROM scenario_runs WHERE cleared_at IS NULL""")
        row = await cur.fetchone()
    if not row:
        return None
    return dict(zip(("scenario_id", "name", "activated_at", "params", "counts",
                     "vessel_ids"), row))


async def recompute_risk(conn: Any, now: datetime, hours: int = 48) -> int:
    """Recompute risk_cells over whatever observations now exist.

    Required after BOTH activate and clear: risk_cells is a stored product, so
    without this the map would keep showing the previous field and the change
    would be invisible.
    """
    from app.core.grid import fetch_observations, index_observations
    from app.core.risk import HAZARDS, compute_risk_field, write_risk_cells

    t0 = now - timedelta(hours=6)
    t1 = now + timedelta(hours=hours)
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT DISTINCT h3_cell FROM observations "
            "WHERE valid_time >= %s AND valid_time < %s", (t0, t1))
        cells = [r[0] for r in await cur.fetchall()]
        await cur.execute(
            "SELECT DISTINCT valid_time FROM observations "
            "WHERE valid_time >= %s AND valid_time < %s ORDER BY 1", (t0, t1))
        times = [r[0] for r in await cur.fetchall()]
    if not cells:
        return 0

    working = sorted({c for cell in cells for c in disk(cell, 1)})
    rows = await fetch_observations(conn, working, t0, t1,
                                    [h.variable for h in HAZARDS])
    field = compute_risk_field(index_observations(rows), cells, times, now=now)
    return await write_risk_cells(conn, field)


async def activate(conn: Any, name: str, now: datetime | None = None
                   ) -> dict[str, Any]:
    """Activate a scenario. One transaction from the caller's perspective."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}. Known: {sorted(SCENARIOS)}")
    existing = await active_run(conn)
    if existing:
        raise RuntimeError(
            f"scenario {existing['name']!r} is already active "
            f"(id {existing['scenario_id']}). Clear it first -- two overlapping "
            f"scenarios would mask each other's rows and neither could be "
            f"cleanly restored."
        )

    spec = SCENARIOS[name]
    now = now or datetime.now(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    scenario_id = f"{name}:{uuid.uuid4().hex[:8]}"

    observations = build_field(spec, start)
    fleet = generate_fleet(FLEET_SIZE)

    keys = sorted({(o.h3_cell, o.valid_time) for o in observations})
    cells = [k[0] for k in keys]
    times = [k[1] for k in keys]
    var_names = [v.value for v in EMITTED]

    async with conn.cursor() as cur:
        await cur.execute("""
            INSERT INTO scenario_runs (scenario_id, name, params, vessel_ids)
            VALUES (%s, %s, %s::jsonb, %s)""",
            (scenario_id, name,
             __import__("json").dumps({
                 "description": spec.description,
                 "vmax_ms": spec.vmax_ms, "rmax_km": spec.rmax_km,
                 "holland_b": spec.holland_b,
                 "track": [{"hours": p.hours, "lat": p.lat, "lon": p.lon}
                           for p in spec.track],
                 "start": start.isoformat(), "hours": HOURS,
                 "bbox": list(SCENARIO_BBOX), "fleet_size": len(fleet),
                 "emitted_variables": var_names,
                 "NOT_A_FORECAST": "parametric idealisation for demonstration",
             }),
             [v.vessel_id for v in fleet]))

        # 1. move the real rows aside
        await cur.execute(_MASK, {"cells": cells, "times": times,
                                  "vars": var_names, "sid": scenario_id,
                                  "scenario_source": SCENARIO_SOURCE})
        masked = cur.rowcount or 0

        # 2. write the storm
        written = await write_observations(conn, observations)

        # 3. write the fleet
        await cur.executemany(_VESSEL, [
            (v.vessel_id, v.name, v.vessel_class, v.cruise_speed, v.draft_m)
            for v in fleet])
        await cur.executemany(_VPOS, [
            (now, v.vessel_id, v.lat, v.lon, v.sog, v.cog,
             cell_for(v.lat, v.lon)) for v in fleet])

    risk_rows = await recompute_risk(conn, now)

    counts = {"observations_written": written, "observations_masked": masked,
              "vessels": len(fleet), "cells": len(set(cells)),
              "risk_cells_recomputed": risk_rows}
    async with conn.cursor() as cur:
        await cur.execute("UPDATE scenario_runs SET counts = %s::jsonb "
                          "WHERE scenario_id = %s",
                          (__import__("json").dumps(counts), scenario_id))

    log.warning("SCENARIO ACTIVE: %s (%s) -- %s", name, scenario_id, counts)
    return {"scenario_id": scenario_id, "name": name, "start": start.isoformat(),
            "counts": counts, "simulated": True}


async def clear(conn: Any, now: datetime | None = None) -> dict[str, Any]:
    """Remove every scenario row and restore the masked real data.

    Idempotent: clearing when nothing is active is a no-op, not an error.
    Reports restored-vs-masked so a mismatch is visible rather than silent.
    """
    now = now or datetime.now(timezone.utc)
    run = await active_run(conn)
    if not run:
        return {"cleared": False, "reason": "no active scenario"}

    sid = run["scenario_id"]
    expected_masked = (run.get("counts") or {}).get("observations_masked")

    async with conn.cursor() as cur:
        # Scenario observations: targeted by source_id, never a bare DELETE.
        await cur.execute("DELETE FROM observations WHERE source_id = %s",
                          (SCENARIO_SOURCE,))
        removed_obs = cur.rowcount or 0

        # Fleet: by the exact recorded ids, never a LIKE pattern.
        ids = run["vessel_ids"] or []
        await cur.execute("DELETE FROM vessel_positions WHERE mmsi = ANY(%s)", (ids,))
        removed_pos = cur.rowcount or 0
        await cur.execute("DELETE FROM vessels WHERE mmsi = ANY(%s)", (ids,))
        removed_vessels = cur.rowcount or 0

        # Put the real world back.
        await cur.execute(_UNMASK, {"sid": sid})
        restored = cur.rowcount or 0

        await cur.execute("SELECT count(*) FROM observations_masked "
                          "WHERE scenario_id = %s", (sid,))
        left_behind = (await cur.fetchone())[0]

        # risk_cells is a DERIVED product, and recompute_risk only rewrites
        # cells that still have observations -- it cannot delete rows whose
        # supporting data has just been removed. Without this, clearing left
        # tens of thousands of stale rows behind, 21k of them flagged
        # simulated with hazard_prob 1.0, and the map still showed the storm
        # after the scenario was gone.
        #
        # Two rules, both self-correcting rather than relying on bookkeeping:
        #   1. any cell flagged simulated was produced by scenario input and
        #      must not outlive the scenario;
        #   2. any cell with no observations behind it any more is stale by
        #      definition.
        await cur.execute("DELETE FROM risk_cells WHERE simulated")
        stale_sim = cur.rowcount or 0
        await cur.execute(
            "DELETE FROM risk_cells rc WHERE NOT EXISTS ("
            "SELECT 1 FROM observations o WHERE o.h3_cell = rc.h3_cell)")
        stale_orphan = cur.rowcount or 0

        await cur.execute("UPDATE scenario_runs SET cleared_at = %s "
                          "WHERE scenario_id = %s", (now, sid))

    risk_rows = await recompute_risk(conn, now)

    result = {
        "cleared": True, "scenario_id": sid, "name": run["name"],
        "observations_removed": removed_obs,
        "observations_restored": restored,
        "expected_restored": expected_masked,
        "restore_matches": (expected_masked is None or restored == expected_masked),
        "masked_rows_left_behind": left_behind,
        "vessel_positions_removed": removed_pos,
        "vessels_removed": removed_vessels,
        "simulated_risk_cells_removed": stale_sim,
        "orphan_risk_cells_removed": stale_orphan,
        "risk_cells_recomputed": risk_rows,
    }
    log.warning("SCENARIO CLEARED: %s", result)
    return result


async def status(conn: Any) -> dict[str, Any]:
    run = await active_run(conn)
    async with conn.cursor() as cur:
        await cur.execute("SELECT count(*) FROM observations WHERE source_id = %s",
                          (SCENARIO_SOURCE,))
        obs = (await cur.fetchone())[0]
        await cur.execute("SELECT count(*) FROM observations_masked")
        masked = (await cur.fetchone())[0]
        await cur.execute("SELECT count(*) FROM risk_cells WHERE simulated")
        sim_cells = (await cur.fetchone())[0]
    return {
        "active": run is not None,
        "scenario_id": run["scenario_id"] if run else None,
        "name": run["name"] if run else None,
        "activated_at": run["activated_at"].isoformat() if run else None,
        "params": run["params"] if run else None,
        "counts": run["counts"] if run else None,
        "scenario_observations": obs,
        "masked_real_observations": masked,
        "simulated_risk_cells": sim_cells,
        "available": sorted(SCENARIOS),
    }
