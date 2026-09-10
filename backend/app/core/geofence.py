"""Deterministic geofencing: where is this boat, and what is it about to cross?

NO LLM TOUCHES THIS. Every number here comes from PostGIS geodesic geometry.
An agent may decide to CALL these functions and may read the results back in a
sentence, but it never produces a distance, a bearing or a verdict.

WHY THE SAFETY MARGINS EXIST
----------------------------
The design requirement is ZERO FALSE NEGATIVES on the IMBL: never tell a boat
it is clear when it is not. A false positive costs a skipper an unnecessary
course change. A false negative costs them their boat, their catch, and
potentially months in a foreign jail -- the India-Sri Lanka line in Palk Bay
is arrested-fishermen territory, not a cartographic abstraction.

"When in doubt, alert" is only meaningful if the doubt is quantified, so it is,
in three named constants below. A verdict of CLEAR means "clear by a margin
that accounts for all three", not "the raw number was bigger than the buffer".

THREE FAILURE RULES, because a geofence that fails quietly is worse than none:
  1. A database error propagates. It never degrades to "no zones nearby".
  2. A zone whose geometry cannot be measured alerts. It is never skipped.
  3. Margins only ever move a verdict TOWARD alerting, never away.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

METRES_PER_NM = 1852.0

# ---------------------------------------------------------------------------
# Uncertainty budget. All values in nautical miles.

#: How far an OPEN-DATA boundary may sit from the official one. MarineRegions
#: is a VLIZ compilation digitised from treaty texts and charts, not the
#: authoritative line. 0.5 NM (~926 m) is a deliberately conservative
#: assumption -- we do not know the true error, and guessing small here is the
#: failure mode that puts a boat over the line.
DATA_UNCERTAINTY_NM: dict[str, float] = {
    "imbl": 0.5,
    "eez": 0.5,
    "territorial_sea": 0.5,
    "contiguous_zone": 0.5,
    "baseline": 0.5,
    # OSM protected-area boundaries are crowd-sourced and often traced from
    # low-resolution imagery, so the budget is larger.
    "mpa": 1.0,
}
DEFAULT_DATA_UNCERTAINTY_NM = 1.0

#: Vessel position error: consumer GPS plus AIS quantisation and report lag.
POSITION_UNCERTAINTY_NM = 0.1

#: Default alert buffer if the caller does not choose one.
DEFAULT_BUFFER_NM = 2.0

VERDICT_INSIDE = "inside"
VERDICT_ALERT = "alert"
VERDICT_CLEAR = "clear"

AUTHORITY_OFFICIAL = "official"

#: Zone types that describe a BOUNDARY rather than a warning. Only these
#: govern whether a distance may be presented as a legal line.
BOUNDARY_ZONE_TYPES = frozenset({
    "imbl", "eez", "territorial_sea", "contiguous_zone", "baseline", "mpa",
})


@dataclass(frozen=True, slots=True)
class ZoneHit:
    zone_id: str
    zone_type: str
    name: str | None
    authority: str
    attribution: str

    inside: bool
    #: Geodesic distance to the zone's EDGE. For a polygon that is its
    #: boundary, not the polygon itself: distance to a polygon you are inside
    #: is 0, which tells a skipper nothing about how close they are to leaving.
    distance_nm: float
    #: distance_nm minus the uncertainty budget, floored at 0. THIS is what the
    #: verdict is computed from.
    effective_distance_nm: float
    margin_nm: float
    #: Compass bearing from the point to the nearest point on the edge.
    bearing_deg: float | None
    closest_lat: float | None
    closest_lon: float | None
    verdict: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GeofenceResult:
    lat: float
    lon: float
    buffer_nm: float
    verdict: str
    inside: list[ZoneHit]
    alerts: list[ZoneHit]
    nearest_by_type: dict[str, ZoneHit]
    attributions: list[str]
    #: True when nothing AUTHORITATIVE bears on this position -- i.e. every
    #: zone the vessel is inside or approaching is open data.
    #:
    #: Computed over inside+alerts, NOT over every zone in nearest_by_type.
    #: The naive version (all hits) flipped to False at Katchatheevu because a
    #: distant IMD warning appeared in nearest_by_type, which reads as "these
    #: boundaries are authoritative" -- the precise misrepresentation this flag
    #: exists to prevent.
    advisory_only: bool
    #: True while every BOUNDARY-type zone is open data, regardless of any
    #: official warning nearby. This is the one that governs whether a
    #: distance-to-IMBL may be presented as legal. Always True today.
    boundaries_advisory_only: bool
    #: Official zones actually bearing on this position, with their mandatory
    #: attribution. Empty when nothing authoritative applies.
    official_alerts: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# One query answers containment, distance, closest point and bearing.
#
# ST_Distance on ::geography is GEODESIC -- metres on the ellipsoid. Doing this
# on plain geometry would return DEGREES, and a "distance" of 0.03 looks
# perfectly reasonable right up until someone reads it as nautical miles.
#
# ST_ClosestPoint and ST_Azimuth are planar. That is acceptable for a BEARING
# at Indian latitudes (sub-degree error) but would not be for the distance,
# which is why the two are computed differently on purpose.
#
# No ST_DWithin prefilter: nearest_by_type needs an unbounded search and the
# table is tens of rows. Add one (the geography GIST indexes are already there)
# when this table grows to thousands.
_SQL = """
WITH p AS (
    SELECT ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326) AS g
)
SELECT z.zone_id,
       z.zone_type,
       z.name,
       z.authority,
       z.attribution,
       z.meta,
       ST_Intersects(z.geom, p.g)                              AS inside,
       ST_Distance(z.edge_geom::geography, p.g::geography)     AS dist_m,
       ST_Y(cp.pt)                                             AS closest_lat,
       ST_X(cp.pt)                                             AS closest_lon,
       degrees(ST_Azimuth(p.g, cp.pt))                         AS bearing_deg
  FROM hazard_zones z
  CROSS JOIN p
  LEFT JOIN LATERAL (SELECT ST_ClosestPoint(z.edge_geom, p.g) AS pt) cp ON TRUE
 -- Only zones IN FORCE. A boundary has no expiry (valid_until IS NULL) and is
 -- always in force; a warning does, and an expired warning served as current
 -- is its own kind of wrong answer -- arguably worse than none, because it
 -- looks like live information.
 WHERE z.valid_until IS NULL OR z.valid_until > now()
 ORDER BY dist_m
"""


def margin_for(zone_type: str) -> float:
    """Total uncertainty budget for a zone type, in nautical miles."""
    return (
        DATA_UNCERTAINTY_NM.get(zone_type, DEFAULT_DATA_UNCERTAINTY_NM)
        + POSITION_UNCERTAINTY_NM
    )


def _hit(row: tuple, buffer_nm: float) -> ZoneHit:
    (zone_id, zone_type, name, authority, attribution, meta,
     inside, dist_m, clat, clon, bearing) = row

    margin = margin_for(zone_type)

    if dist_m is None:
        # RULE 2: unmeasurable geometry alerts. A zone we cannot evaluate is
        # not a zone we are clear of.
        log.error("zone %s has no measurable distance -- alerting by default", zone_id)
        return ZoneHit(
            zone_id=zone_id, zone_type=zone_type, name=name, authority=authority,
            attribution=attribution, inside=bool(inside), distance_nm=0.0,
            effective_distance_nm=0.0, margin_nm=margin, bearing_deg=None,
            closest_lat=None, closest_lon=None, verdict=VERDICT_ALERT,
            meta={**(meta or {}), "error": "distance could not be computed"},
        )

    distance_nm = dist_m / METRES_PER_NM
    # RULE 3: the margin only ever shrinks the distance, so it can only ever
    # turn a CLEAR into an ALERT, never the reverse.
    effective = max(0.0, distance_nm - margin)

    if inside:
        verdict = VERDICT_INSIDE
    elif effective <= buffer_nm:
        verdict = VERDICT_ALERT
    else:
        verdict = VERDICT_CLEAR

    return ZoneHit(
        zone_id=zone_id, zone_type=zone_type, name=name, authority=authority,
        attribution=attribution, inside=bool(inside),
        distance_nm=distance_nm, effective_distance_nm=effective, margin_nm=margin,
        bearing_deg=(bearing % 360.0) if bearing is not None else None,
        closest_lat=clat, closest_lon=clon, verdict=verdict, meta=meta or {},
    )


async def _all_hits(conn: Any, lat: float, lon: float, buffer_nm: float) -> list[ZoneHit]:
    # RULE 1: no try/except. A database failure raises out of here. Returning
    # an empty list on error would read to every caller as "nothing nearby".
    async with conn.cursor() as cur:
        await cur.execute(_SQL, {"lat": lat, "lon": lon})
        rows = await cur.fetchall()
    return [_hit(r, buffer_nm) for r in rows]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def point_in_zone(conn: Any, lat: float, lon: float) -> list[ZoneHit]:
    """Zones geometrically containing the point.

    Only polygon zones can contain a point. A boundary LINE (the IMBL) never
    will -- crossing it is a proximity question, not a containment one, which
    is exactly why proximity_alert exists.
    """
    return [h for h in await _all_hits(conn, lat, lon, DEFAULT_BUFFER_NM) if h.inside]


async def distance_to_zone(conn: Any, lat: float, lon: float, zone_type: str) -> ZoneHit | None:
    """Nearest zone of a given type, or None if none are loaded.

    None means "no zones of this type in the database" -- an empty table, not a
    clear sea. Callers must not render it as safe.
    """
    hits = [h for h in await _all_hits(conn, lat, lon, DEFAULT_BUFFER_NM)
            if h.zone_type == zone_type]
    return min(hits, key=lambda h: h.distance_nm) if hits else None


async def proximity_alert(
    conn: Any, lat: float, lon: float, buffer_nm: float = DEFAULT_BUFFER_NM
) -> GeofenceResult:
    """Full geofence assessment for one position."""
    hits = await _all_hits(conn, lat, lon, buffer_nm)

    inside = [h for h in hits if h.inside]
    alerts = [h for h in hits if h.verdict in (VERDICT_ALERT, VERDICT_INSIDE)]
    alerts.sort(key=lambda h: (h.verdict != VERDICT_INSIDE, h.effective_distance_nm))

    nearest: dict[str, ZoneHit] = {}
    for h in sorted(hits, key=lambda h: h.distance_nm):
        nearest.setdefault(h.zone_type, h)

    if inside:
        verdict = VERDICT_INSIDE
    elif alerts:
        verdict = VERDICT_ALERT
    else:
        verdict = VERDICT_CLEAR

    # Only zones the vessel is actually inside or approaching count toward
    # whether something authoritative applies. See the field comment.
    relevant = inside + [h for h in alerts if not h.inside]
    official = [h for h in relevant if h.authority == AUTHORITY_OFFICIAL]

    return GeofenceResult(
        lat=lat, lon=lon, buffer_nm=buffer_nm, verdict=verdict,
        inside=inside, alerts=alerts, nearest_by_type=nearest,
        attributions=sorted({h.attribution for h in hits}),
        advisory_only=not official,
        # Boundary data is advisory regardless of any warning nearby.
        boundaries_advisory_only=all(
            h.authority != AUTHORITY_OFFICIAL
            for h in hits if h.zone_type in BOUNDARY_ZONE_TYPES
        ),
        official_alerts=[{
            "zone_id": h.zone_id, "zone_type": h.zone_type, "name": h.name,
            "verdict": h.verdict, "distance_nm": round(h.distance_nm, 2),
            "attribution": h.attribution,
            "severity": (h.meta or {}).get("severity"),
            "event": (h.meta or {}).get("event"),
            "expires": (h.meta or {}).get("expires"),
            "sender_name": (h.meta or {}).get("sender_name"),
        } for h in official],
    )
