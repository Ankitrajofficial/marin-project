"""MarineRegions (VLIZ) loader: EEZ, territorial sea, and treaty boundary lines.

WHAT THIS PROVIDES
------------------
The India-Sri Lanka IMBL, which for a Tamil Nadu fisherman is the single most
consequential line on the map. It is present as three treaty segments:

    1974 Agreement on the Boundary in Historic Waters   (Palk Bay, ~158 km)
    1976 Agreements on the Maritime Boundary            (Gulf of Mannar,
                                                         Bay of Bengal)

Plus India's 12 NM territorial sea, 24 NM contiguous zone, 200 NM EEZ, and the
straight baselines from the 2009 MEA notification.

WHAT IT IS NOT -- READ THIS
---------------------------
MarineRegions is a scientific compilation maintained by VLIZ under CC-BY 4.0.
It is NOT Survey of India, NOT the Indian Navy, NOT the Coast Guard, and it
carries NO legal authority. India regulates how its boundaries may be depicted.

Every record here is written with authority='open_data_advisory', and the API
refuses to serve a geofence result without that flag. Nothing in ORCA may
present these lines as the legal boundary -- not the map, not a panel, not an
agent's sentence. Enforcement is structural rather than a matter of care,
because "be careful" does not survive six months of feature work.

THE AXIS-ORDER TRAP
-------------------
This GeoServer takes WFS bbox as lon,lat despite EPSG:4326's lat,lon
convention. A lat,lon bbox for the Palk Strait (8,78 -> 10.5,81.5) returns
SVALBARD -- 8-10.5 degrees EAST, 78-81.5 degrees NORTH. Real coordinates,
real features, wrong hemisphere, no error. Verified by probe.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.zones.base import (
    AUTHORITY_ADVISORY,
    ZONE_BASELINE,
    ZONE_CONTIGUOUS,
    ZONE_EEZ,
    ZONE_IMBL,
    ZONE_TERRITORIAL_SEA,
    ZoneRecord,
)

log = logging.getLogger(__name__)

WFS = "https://geo.vliz.be/geoserver/MarineRegions/wfs"
SOURCE_ID = "marine_regions"
ATTRIBUTION = (
    "Maritime boundaries © Flanders Marine Institute (VLIZ), MarineRegions.org, "
    "CC-BY 4.0. Advisory only — not an official or legal boundary."
)
LICENSE = "CC-BY 4.0"
TIMEOUT_S = 180.0

# lon,lat,lon,lat -- India's maritime neighbourhood. See the axis note above.
INDIA_BBOX = (66.0, 5.0, 94.0, 25.0)

# MarineRegions line_type -> our zone_type. A treaty line IS the IMBL.
_LINE_TYPE_MAP = {
    "Treaty": ZONE_IMBL,
    "Straight baseline": ZONE_BASELINE,
    "200 NM": ZONE_EEZ,
    "Median line": ZONE_IMBL,
    "Joint regime": ZONE_IMBL,
    # An arbitral award is as binding a maritime boundary as a treaty -- the
    # India/Bangladesh boundary in the Bay of Bengal is a 2014 PCA award, not a
    # treaty. Dropping it would leave a real boundary invisible to the geofence.
    "Court ruling": ZONE_IMBL,
}
# 'Connection line' is deliberately absent: it is a cartographic join between
# segments, not a boundary anyone can cross.


async def _wfs(client: httpx.AsyncClient, layer: str, **params: Any) -> dict[str, Any]:
    r = await client.get(
        WFS,
        params={
            "service": "WFS",
            "version": "1.1.0",
            "request": "GetFeature",
            "typeName": f"MarineRegions:{layer}",
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            **params,
        },
    )
    r.raise_for_status()
    return r.json()


async def fetch_boundaries(client: httpx.AsyncClient) -> list[ZoneRecord]:
    """Treaty lines, baselines and 200 NM limits touching India."""
    w, s, e, n = INDIA_BBOX
    body = await _wfs(
        client, "eez_boundaries", bbox=f"{w},{s},{e},{n},EPSG:4326"
    )

    zones: list[ZoneRecord] = []
    for f in body.get("features", []):
        p = f["properties"]
        # Keep only lines India is actually a party to. The bbox catches
        # neighbours' boundaries with each other too.
        if "India" not in {p.get("territory1"), p.get("territory2"),
                           p.get("sovereign1"), p.get("sovereign2")}:
            continue

        zone_type = _LINE_TYPE_MAP.get(p.get("line_type"))
        if zone_type is None:
            log.info("skipping unmapped line_type %r", p.get("line_type"))
            continue

        other = p.get("territory2") if p.get("territory1") == "India" else p.get("territory1")
        zones.append(
            ZoneRecord(
                zone_id=f"mr:line:{p['line_id']}",
                zone_type=zone_type,
                name=p.get("line_name") or f"{p.get('territory1')} – {p.get('territory2')}",
                geometry=f["geometry"],
                authority=AUTHORITY_ADVISORY,
                attribution=ATTRIBUTION,
                license=LICENSE,
                source_id=SOURCE_ID,
                source_url=p.get("url1"),
                meta={
                    "line_id": p.get("line_id"),
                    "line_type": p.get("line_type"),
                    "counterpart": other,
                    "treaty_date": p.get("doc_date"),
                    "treaty_source": p.get("source1"),
                    "length_km": p.get("length_km"),
                },
            )
        )
    return zones


_POLY_LAYERS = (
    ("eez", ZONE_EEZ, "Indian EEZ (200 NM)"),
    ("eez_24nm", ZONE_CONTIGUOUS, "Indian contiguous zone (24 NM)"),
    ("eez_12nm", ZONE_TERRITORIAL_SEA, "Indian territorial sea (12 NM)"),
)


async def fetch_polygons(client: httpx.AsyncClient) -> list[ZoneRecord]:
    """India's EEZ / contiguous zone / territorial sea polygons."""
    zones: list[ZoneRecord] = []
    for layer, zone_type, label in _POLY_LAYERS:
        body = await _wfs(client, layer, CQL_FILTER="territory1='India'")
        for f in body.get("features", []):
            p = f["properties"]
            zones.append(
                ZoneRecord(
                    zone_id=f"mr:{layer}:{p.get('mrgid') or p.get('mrgid_eez') or 'india'}",
                    zone_type=zone_type,
                    name=p.get("geoname") or label,
                    geometry=f["geometry"],
                    authority=AUTHORITY_ADVISORY,
                    attribution=ATTRIBUTION,
                    license=LICENSE,
                    source_id=SOURCE_ID,
                    source_url=p.get("url"),
                    meta={"layer": layer, "mrgid": p.get("mrgid"),
                          "area_km2": p.get("area_km2")},
                )
            )
    return zones


async def fetch_all() -> list[ZoneRecord]:
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        lines = await fetch_boundaries(client)
        polys = await fetch_polygons(client)
    log.info("marine_regions: %d boundary lines, %d polygons", len(lines), len(polys))
    return lines + polys
