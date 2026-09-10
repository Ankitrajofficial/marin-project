"""INCOIS fishing landing centres, as recall destinations.

WHY THESE AND NOT JUST OSM HARBOURS
-----------------------------------
OSM gave 57 named harbours in the AOI, weighted toward ports and marinas --
infrastructure for ships and pleasure craft. A fishing boat running from a
cyclone does not go to a container terminal; it goes to the landing centre it
works out of. INCOIS maintains the actual list, and it is an Indian government
dataset for an Indian problem statement.

Both sources are kept. harbours.source_id records which, so a recall trace can
say whether the destination it chose came from INCOIS or from OpenStreetMap.

VERIFIED REACHABLE (workspace-scoped WFS; the top-level WFS GetCapabilities
is 403):

    https://incois.gov.in/geoserver/PFZ_LandingCentres/wfs
        ?service=WFS&version=1.0.0&request=GetFeature
        &typeName=PFZ_LandingCentres:LandingCenters_29Apr2024
        &outputFormat=application/json
    -> HTTP 200, 811 KB, 1,223 features

THE LAYER IS FROZEN, AND THAT IS RECORDED NOT HIDDEN
----------------------------------------------------
Every record carries FORECAST_D = 2024-04-27, and the layer is literally named
LandingCenters_29Apr2024. It is a SNAPSHOT, not a feed. Landing centres do not
move much, so the LOCATIONS remain useful -- but the snapshot date is stored
and surfaced as the data age, so nothing presents this as live. The PFZ
advisory fields in the same rows (bearing, distance, depth to the fishing zone)
are 2+ years stale and are stored only as provenance; nothing computes with
them.

DEPTH_FROM / DEPTH_TO ARE NOT HARBOUR DEPTHS
--------------------------------------------
They are the depth range of the potential FISHING ZONE the advisory pointed
to -- tens of nautical miles offshore -- not the usable depth at the landing
centre. Loading them into harbours.depth_m would manufacture a draught check
out of a completely unrelated number, which is the same failure as using
GEBCO for harbour depth. depth_source stays 'unknown'.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

SOURCE_ID = "incois_lc"
WFS = "https://incois.gov.in/geoserver/PFZ_LandingCentres/wfs"
TYPENAME = "PFZ_LandingCentres:LandingCenters_29Apr2024"
TIMEOUT_S = 180.0

ATTRIBUTION = (
    "Fishing landing centres © Indian National Centre for Ocean Information "
    "Services (INCOIS), Ministry of Earth Sciences, Government of India."
)

#: Our AOI sectors, as INCOIS names them.
AOI_SECTORS = {"KERALA", "NORTH TAMILNADU", "SOUTH TAMILNADU"}

#: The snapshot the layer is frozen at, from FORECAST_D on every record and
#: from the layer name itself. Surfaced as the data age.
SNAPSHOT_DATE = "2024-04-27"


async def fetch_all(sectors: set[str] | None = None) -> list[dict[str, Any]]:
    sectors = sectors or AOI_SECTORS
    headers = {"User-Agent": "ORCA/0.1 (SIH 2026 marine hazard engine)"}
    params = {
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": TYPENAME, "outputFormat": "application/json",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers) as client:
        r = await client.get(WFS, params=params)
        r.raise_for_status()
        body = r.json()

    rows, skipped = [], 0
    for f in body.get("features", []):
        p = f.get("properties") or {}
        sector = (p.get("SECTOR_NAM") or "").strip().upper()
        if sector not in sectors:
            continue
        name = (p.get("LC_NAME") or "").strip()
        lat, lon = p.get("LATITUDE"), p.get("LONGITUDE")
        if not name or lat is None or lon is None:
            skipped += 1
            continue
        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skipped += 1
            continue

        uid = (p.get("LC_UNIQUE_") or p.get("OBJECTID") or f"{lat},{lon}")
        rows.append({
            "harbour_id": f"incois:lc:{uid}",
            "name": name,
            "lat": lat, "lon": lon,
            # NOT from DEPTH_FROM/DEPTH_TO -- see the module docstring.
            "depth_m": None,
            "depth_source": "unknown",
            "harbour_type": "landing_centre",
            "capacity": None,
            "source_id": SOURCE_ID,
            "source_url": f"{WFS}?request=GetFeature&typeName={TYPENAME}",
            "meta": {
                "sector": p.get("SECTOR_NAM"),
                "district": p.get("DIST_NAME"),
                "lc_unique_id": p.get("LC_UNIQUE_"),
                "marine_fishing": p.get("MARINE_FIS"),
                # Stored as PROVENANCE only. Nothing computes with these --
                # they describe a 2024 fishing advisory, not this harbour.
                "pfz_advisory_2024": {
                    "forecast_date": p.get("FORECAST_D"),
                    "validity_date": p.get("VALIDITY_D"),
                    "direction": p.get("DIRECTION"),
                    "bearing": p.get("BEARING"),
                    "distance_from_nm": p.get("DISTANCE_F"),
                    "distance_to_nm": p.get("DISTANCE_T"),
                    "zone_depth_from_m": p.get("DEPTH_FROM"),
                    "zone_depth_to_m": p.get("DEPTH_TO"),
                },
                # The honest bit: this is a snapshot, not a feed.
                "snapshot_date": SNAPSHOT_DATE,
                "is_snapshot": True,
                "attribution": ATTRIBUTION,
            },
        })

    log.info("incois_lc: %d landing centres in %s (%d skipped for missing "
             "name/coords). Layer frozen at %s -- locations only, not live.",
             len(rows), sorted(sectors), skipped, SNAPSHOT_DATE)
    return rows
