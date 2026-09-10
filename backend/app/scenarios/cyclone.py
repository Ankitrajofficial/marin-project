"""Parametric tropical cyclone field.

WHAT THIS IS
------------
An idealised vortex that produces a physically plausible wind and wave field
so the rest of ORCA has something to compute against. Every relation below is
a published parameterisation, cited, with its constants in one block.

WHAT THIS IS NOT
----------------
A forecast. It is not initialised from observations, it assimilates nothing,
and it predicts nothing. Its only purpose is to exercise the pipeline with a
field that behaves like a storm instead of arbitrary large numbers. Every row
it produces is written under source_id 'scenario_sim', which flags
simulated=true on every risk cell, recall entry and trace downstream. It must
never be presented as a forecast of anything.

THE PARAMETERISATION
--------------------
Wind, radial profile -- Holland (1980), the standard parametric TC vortex:

    V(r) = Vmax * sqrt( (Rmax/r)^B * exp(1 - (Rmax/r)^B) )

  peaks at Vmax where r = Rmax, and falls to zero in the eye (r -> 0), which is
  why the eye of this storm is calm rather than catastrophic. B (the Holland
  shape parameter) is typically 1.0-2.5; higher means a tighter, more peaked
  eyewall.

Track asymmetry -- a moving cyclone is stronger on the right of its track in
  the northern hemisphere, because translation adds to the tangential wind on
  that side and opposes it on the other. Added vectorially, scaled by the local
  wind fraction so it matters most near the eyewall. This is a real feature and
  it shows up in the recall ranking as boats on one side having less time.

Wind direction -- cyclonic (counter-clockwise in the NH) tangential flow with a
  ~22 degree inflow angle toward the eye, the standard boundary-layer value.

Wave height -- Pierson-Moskowitz fully-developed sea, Hs = 0.0214 * U^2,
  multiplied by a fetch/duration-limited reduction. The reduction matters: raw
  PM at 30 m/s gives ~19 m, which is a fully developed Southern Ocean swell,
  not the sea under a cyclone that has been over a given patch of water for a
  few hours. 0.55 is a conservative development factor for a moving storm.

Wave period -- Tz = 3.55 * sqrt(Hs) and Tp = 1.2 * Tz, the standard engineering
  approximations relating period to height in a wind sea.

Waves are taken to run with the wind. Real swell propagates out of the storm
and arrives before it; that refinement is not modelled here, and its absence
makes this scenario OPTIMISTIC about how early conditions deteriorate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Constants, all documented above.
HOLLAND_B = 1.6                 # shape parameter, mid of the usual 1.0-2.5
INFLOW_ANGLE_DEG = 22.0         # boundary-layer inflow toward the eye
TRANSLATION_FRACTION = 0.5      # of translation speed added on the strong side
PM_COEFF = 0.0214               # Pierson-Moskowitz Hs = PM_COEFF * U^2
FETCH_REDUCTION = 0.45          # moving storm is not a fully developed sea
TZ_COEFF = 3.55                 # Tz = TZ_COEFF * sqrt(Hs)
TP_OVER_TZ = 1.2

#: Physical ceilings. Not to make the storm pretty -- to stop a numerical
#: artefact near r=0 producing a value the Observation contract would reject
#: and the whole activation failing halfway through.
#:
#: KNOWN LIMITATION: within roughly Rmax the PM relation exceeds MAX_WAVE_M, so
#: inside the eyewall the CEILING is what sets Hs, not the parameterisation.
#: Outside ~1.5*Rmax the physics does the work. Said out loud because a capped
#: value looks exactly like a computed one.
MAX_WIND_MS = 90.0
MAX_WAVE_M = 18.0

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True, slots=True)
class TrackPoint:
    hours: float          # hours from scenario start
    lat: float
    lon: float


@dataclass(frozen=True, slots=True)
class CycloneSpec:
    name: str
    vmax_ms: float                    # max sustained wind at the eyewall
    rmax_km: float                    # radius of maximum wind
    track: tuple[TrackPoint, ...]
    holland_b: float = HOLLAND_B
    #: Beyond this the vortex contributes nothing, so distant cells keep a
    #: calm field instead of a tiny artificial breeze.
    outer_radius_km: float = 400.0
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing FROM point 1 TO point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(x, y)) % 360.0


def eye_at(spec: CycloneSpec, hours: float) -> tuple[float, float, float, float]:
    """Eye position and translation vector at `hours` from start.

    Returns (lat, lon, translation_speed_ms, translation_bearing_deg).
    Linear interpolation between waypoints -- a real track curves, this does
    not, and that is one of the ways this is an idealisation.
    """
    pts = spec.track
    hours = max(pts[0].hours, min(pts[-1].hours, hours))
    for a, b in zip(pts, pts[1:]):
        if a.hours <= hours <= b.hours:
            span = (b.hours - a.hours) or 1e-9
            f = (hours - a.hours) / span
            lat = a.lat + (b.lat - a.lat) * f
            lon = a.lon + (b.lon - a.lon) * f
            dist_m = haversine_m(a.lat, a.lon, b.lat, b.lon)
            speed = dist_m / (span * 3600.0)
            return lat, lon, speed, bearing_deg(a.lat, a.lon, b.lat, b.lon)
    last = pts[-1]
    return last.lat, last.lon, 0.0, 0.0


def wind_at(spec: CycloneSpec, lat: float, lon: float, hours: float
            ) -> tuple[float, float]:
    """(speed m/s, direction the wind blows FROM, degrees) at a point.

    'From' is the meteorological convention, which is what
    adapters/base.py's Variable.WIND_DIRECTION means. Emitting 'toward' here
    would reverse every vector downstream and no validator could catch it.
    """
    eye_lat, eye_lon, trans_ms, trans_brg = eye_at(spec, hours)
    r_km = haversine_m(eye_lat, eye_lon, lat, lon) / 1000.0

    if r_km >= spec.outer_radius_km:
        return 0.0, 0.0
    # Inside the eye the profile tends to zero; guard the division too.
    r_km = max(r_km, 0.5)

    ratio = (spec.rmax_km / r_km) ** spec.holland_b
    v = spec.vmax_ms * math.sqrt(ratio * math.exp(1.0 - ratio))

    # Bearing from the eye out to this point.
    theta = bearing_deg(eye_lat, eye_lon, lat, lon)
    # Cyclonic (counter-clockwise, NH): due east of the eye the wind blows
    # north, i.e. toward theta - 90. Then rotate toward the eye by the inflow
    # angle.
    toward = (theta - 90.0 - INFLOW_ANGLE_DEG) % 360.0

    # Translation, added vectorially and weighted by the local wind fraction.
    if trans_ms > 0 and spec.vmax_ms > 0:
        w = TRANSLATION_FRACTION * trans_ms * (v / spec.vmax_ms)
        vx = v * math.sin(math.radians(toward)) + w * math.sin(math.radians(trans_brg))
        vy = v * math.cos(math.radians(toward)) + w * math.cos(math.radians(trans_brg))
        v = math.hypot(vx, vy)
        toward = math.degrees(math.atan2(vx, vy)) % 360.0

    v = min(v, MAX_WIND_MS)
    # Convert 'toward' to the 'from' convention.
    return v, (toward + 180.0) % 360.0


def waves_from_wind(wind_ms: float) -> tuple[float, float]:
    """(Hs metres, Tp seconds) from wind speed. See the module docstring."""
    hs = min(PM_COEFF * wind_ms * wind_ms * FETCH_REDUCTION, MAX_WAVE_M)
    if hs <= 0.05:
        return 0.0, 0.0
    tz = TZ_COEFF * math.sqrt(hs)
    return hs, tz * TP_OVER_TZ


# ---------------------------------------------------------------------------
# The demo storm.
#
# Track: forms in the central Bay of Bengal and runs WNW toward the northern
# Tamil Nadu coast over 24 hours, which is the classic post-monsoon Bay of
# Bengal pattern (the tracks that hit Nagapattinam, Cuddalore and Chennai).
# ~19 km/h translation and Vmax 45 m/s put it in the "very severe cyclonic
# storm" band of the IMD scale.
BAY_OF_BENGAL_CYCLONE = CycloneSpec(
    name="bay_of_bengal_cyclone",
    vmax_ms=45.0,
    rmax_km=45.0,
    track=(
        TrackPoint(0.0, 10.2, 84.5),
        TrackPoint(8.0, 10.9, 83.0),
        TrackPoint(16.0, 11.6, 81.4),
        TrackPoint(24.0, 12.2, 79.9),
    ),
    description=(
        "Idealised very severe cyclonic storm crossing the Bay of Bengal "
        "toward the northern Tamil Nadu coast over 24 h. Holland (1980) wind "
        "profile, Vmax 45 m/s, Rmax 45 km. NOT A FORECAST."
    ),
)

SCENARIOS: dict[str, CycloneSpec] = {
    BAY_OF_BENGAL_CYCLONE.name: BAY_OF_BENGAL_CYCLONE,
}
