"""A simulated fleet of small coastal fishing craft.

WHY THIS EXISTS
---------------
Live AIS over the AOI carries cargo ships and tankers. Small fishing craft are
largely not required to carry AIS and do not appear -- and they are precisely
the vessels a cyclone recall list exists for. A recall demo built on five
container ships parked outside Chennai tests nothing that matters.

So this generates a fleet with characteristics drawn from the actual
Kerala-Tamil Nadu fishery, and every one of them is labelled as synthetic in a
way no human or query can mistake:

    IDS ARE NOT MMSI-SHAPED. They look like 'SIM-0007', not '419001852'.

A plausible fake MMSI in a maritime database outlives the demo that created it
and is eventually believed by someone. 'SIM-' cannot be.

VESSEL CLASSES, and where the numbers come from
-----------------------------------------------
Indian marine fisheries craft fall into three broad groups, and the ones that
matter for recall are the motorised and mechanised coastal boats:

  frp_outboard   Fibreglass boats with outboard motors. The most numerous
                 category. Shallow draught, relatively fast, but very
                 vulnerable -- they work 5-25 NM out and have no shelter.
  small_mech     Small mechanised boats, day trips.
  trawler        Mechanised trawlers, deeper draught, slower, working further
                 offshore for longer.

Speeds are transit speeds, not trawling speeds -- a boat running for shelter is
not towing gear.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

METRES_PER_NM = 1852.0

# (class, draught range m, transit speed range m/s, share of fleet)
VESSEL_CLASSES: tuple[tuple[str, tuple[float, float], tuple[float, float], float], ...] = (
    ("frp_outboard", (0.6, 1.2), (3.4, 4.6), 0.50),   # ~7-9 kn
    ("small_mech",   (1.1, 1.8), (2.8, 3.4), 0.30),   # ~5.5-6.6 kn
    ("trawler",      (1.8, 2.6), (2.2, 2.7), 0.20),   # ~4.3-5.2 kn
)

#: Coastal anchors with the bearing that points OUT TO SEA from each. Vessels
#: are scattered seaward from these, so nothing is generated on land -- there
#: is no coastline polygon in play, and a boat placed inland would silently
#: become a cell with no marine data.
#: East coast only: the demo cyclone is a Bay of Bengal storm, and a fleet on
#: the Arabian Sea side would be unaffected by it.
ANCHORS: tuple[tuple[str, float, float, float], ...] = (
    ("Tiruchendur",    8.50, 78.15, 110.0),
    ("Tuticorin",      8.80, 78.25, 100.0),
    ("Gulf of Mannar", 9.10, 79.05,  95.0),
    ("Rameswaram",     9.30, 79.40,  80.0),
    ("Point Calimere",10.30, 79.90,  70.0),
    ("Nagapattinam",  10.77, 79.87,  85.0),
    ("Karaikal",      10.92, 79.85,  85.0),
    ("Cuddalore",     11.72, 79.78,  90.0),
    ("Puducherry",    11.93, 79.83,  95.0),
    ("Chennai",       13.05, 80.29, 100.0),
)

#: How far out the fleet works, nautical miles.
MIN_OFFSHORE_NM = 4.0
MAX_OFFSHORE_NM = 55.0


@dataclass(frozen=True, slots=True)
class SimVessel:
    vessel_id: str
    name: str
    vessel_class: str
    draft_m: float
    cruise_speed: float      # m/s
    lat: float
    lon: float
    sog: float               # m/s, current speed over ground
    cog: float               # degrees
    home: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "vessel_id": self.vessel_id, "name": self.name,
            "vessel_class": self.vessel_class, "draft_m": self.draft_m,
            "cruise_speed": self.cruise_speed, "lat": self.lat, "lon": self.lon,
            "sog": self.sog, "cog": self.cog, "home": self.home,
        }


def _offset(lat: float, lon: float, bearing_deg: float, distance_nm: float
            ) -> tuple[float, float]:
    """Move a point along a bearing. Small-angle flat-earth is fine at these
    distances (<60 NM) and keeps the generator readable."""
    d_deg = distance_nm / 60.0                       # 1 NM ~ 1 arc-minute
    b = math.radians(bearing_deg)
    dlat = d_deg * math.cos(b)
    dlon = d_deg * math.sin(b) / max(0.2, math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


def generate(n: int = 50, seed: int = 26176) -> list[SimVessel]:
    """A deterministic fleet.

    Seeded so a demo is reproducible: the same scenario produces the same
    boats in the same places, and a ranking someone saw yesterday can be shown
    again today.
    """
    rng = random.Random(seed)
    classes, weights = zip(*[(c[0], c[3]) for c in VESSEL_CLASSES])
    by_name = {c[0]: c for c in VESSEL_CLASSES}

    fleet: list[SimVessel] = []
    for i in range(n):
        cls = rng.choices(classes, weights=weights, k=1)[0]
        _, draft_rng, speed_rng, _ = by_name[cls]

        home, alat, alon, seaward = rng.choice(ANCHORS)
        # Trawlers work further out than open FRP boats.
        far = 1.0 if cls == "trawler" else (0.75 if cls == "small_mech" else 0.5)
        dist = rng.uniform(MIN_OFFSHORE_NM, MIN_OFFSHORE_NM +
                           (MAX_OFFSHORE_NM - MIN_OFFSHORE_NM) * far)
        bearing = seaward + rng.uniform(-38.0, 38.0)
        lat, lon = _offset(alat, alon, bearing, dist)

        cruise = rng.uniform(*speed_rng)
        fleet.append(SimVessel(
            vessel_id=f"SIM-{i + 1:04d}",
            name=f"SIM {cls.replace('_', ' ').title()} {i + 1:02d}",
            vessel_class=cls,
            draft_m=round(rng.uniform(*draft_rng), 2),
            cruise_speed=round(cruise, 2),
            lat=round(lat, 5), lon=round(lon, 5),
            # Working speed, not transit speed: these boats are fishing when
            # the warning goes out, which is the whole point.
            sog=round(rng.uniform(0.0, 1.6), 2),
            cog=round(rng.uniform(0, 360), 1),
            home=home,
        ))
    return fleet
