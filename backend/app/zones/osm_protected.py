"""OSM Overpass loader: marine protected areas.

WHY OSM AND NOT WDPA
--------------------
The canonical MPA dataset is WDPA / Protected Planet (UNEP-WCMC). Its API
returns HTTP 401 without a registered token (verified), which makes it Tier 2
in CLAUDE.md's terms and unusable for a demo that must work from a clean
clone. OSM is Tier 1: no key, works today, and it has the polygon that
actually matters here -- Gulf of Mannar Marine National Park.

The trade is accuracy of designation. OSM protected areas are crowd-sourced:
boundaries are approximate, protect_class is inconsistently tagged, and
coverage is uneven. That is precisely why these load as
authority='open_data_advisory' alongside everything else. If WDPA credentials
appear later, this module is the only thing that needs replacing.

GEOMETRY NOTE
-------------
Overpass `out geom` gives way/relation member coordinates, not assembled
polygons. Multipolygon relations must be stitched from their outer ways --
handled below, with unclosed rings closed explicitly. A relation whose rings
cannot be assembled is SKIPPED WITH A WARNING rather than emitted half-built:
a protected-area boundary that is subtly wrong is worse than one that is
absent, because absent is visible.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.zones.base import AUTHORITY_ADVISORY, ZONE_MPA, ZoneRecord

log = logging.getLogger(__name__)

OVERPASS = "https://overpass-api.de/api/interpreter"
SOURCE_ID = "overpass"
ATTRIBUTION = (
    "Protected areas © OpenStreetMap contributors, ODbL. Crowd-sourced; "
    "advisory only — not an official protected-area designation."
)
LICENSE = "ODbL 1.0"
TIMEOUT_S = 300.0

# south,west,north,east -- Overpass bbox order is lat-first, the OPPOSITE of
# the MarineRegions WFS bbox in the sibling module. Two upstreams, two
# conventions; both are stated where they are used.
DEFAULT_BBOX = (7.0, 72.0, 14.0, 81.5)

_QUERY = """
[out:json][timeout:240];
(
  relation["boundary"="protected_area"]({s},{w},{n},{e});
  relation["leisure"="nature_reserve"]({s},{w},{n},{e});
  way["boundary"="protected_area"]({s},{w},{n},{e});
  way["leisure"="nature_reserve"]({s},{w},{n},{e});
);
out geom;
"""

# Keep only areas that are plausibly marine/coastal. OSM in this bbox is full
# of inland bird sanctuaries which are not geofencing concerns for a boat.
_MARINE_HINTS = ("marine", "sea", "gulf", "coast", "mangrove", "island",
                 "reef", "bay", "backwater", "lagoon", "estuar")


def _ring(coords: list[dict[str, float]]) -> list[list[float]]:
    ring = [[c["lon"], c["lat"]] for c in coords]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring


def _geometry(element: dict[str, Any]) -> dict[str, Any] | None:
    """Assemble a GeoJSON polygon from an Overpass element, or None."""
    if element["type"] == "way":
        ring = _ring(element.get("geometry") or [])
        return {"type": "Polygon", "coordinates": [ring]} if len(ring) >= 4 else None

    polys = []
    for member in element.get("members", []):
        if member.get("role") != "outer" or not member.get("geometry"):
            continue
        ring = _ring(member["geometry"])
        if len(ring) >= 4:
            polys.append([ring])
    if not polys:
        return None
    return ({"type": "Polygon", "coordinates": polys[0]} if len(polys) == 1
            else {"type": "MultiPolygon", "coordinates": polys})


def _is_marine(tags: dict[str, str]) -> bool:
    blob = " ".join(
        str(tags.get(k, "")) for k in ("name", "name:en", "protection_title", "designation")
    ).lower()
    return any(h in blob for h in _MARINE_HINTS)


async def fetch_all(bbox: tuple[float, float, float, float] = DEFAULT_BBOX) -> list[ZoneRecord]:
    s, w, n, e = bbox
    # Overpass wants the query as the RAW request body. httpx's data= with a
    # string form-encodes it, which Overpass answers with 406 Not Acceptable.
    # content= sends it verbatim. The User-Agent is requested by Overpass's
    # usage policy so operators can identify traffic.
    headers = {"User-Agent": "ORCA/0.1 (SIH 2026 marine hazard engine)",
               "Content-Type": "text/plain; charset=utf-8"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers) as client:
        r = await client.post(
            OVERPASS, content=_QUERY.format(s=s, w=w, n=n, e=e).encode("utf-8")
        )
        r.raise_for_status()
        body = r.json()

    zones, skipped = [], 0
    for el in body.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or not _is_marine(tags):
            continue

        geometry = _geometry(el)
        if geometry is None:
            skipped += 1
            log.warning(
                "osm %s/%s (%s): could not assemble rings -- SKIPPED. An absent "
                "protected area is visible; a subtly wrong one is not.",
                el["type"], el["id"], name,
            )
            continue

        zones.append(
            ZoneRecord(
                zone_id=f"osm:{el['type']}:{el['id']}",
                zone_type=ZONE_MPA,
                name=name,
                geometry=geometry,
                authority=AUTHORITY_ADVISORY,
                attribution=ATTRIBUTION,
                license=LICENSE,
                source_id=SOURCE_ID,
                source_url=f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
                meta={
                    "osm_type": el["type"], "osm_id": el["id"],
                    "protect_class": tags.get("protect_class"),
                    "protection_title": tags.get("protection_title"),
                    "designation": tags.get("designation"),
                },
            )
        )

    log.info("osm_protected: %d marine areas, %d skipped", len(zones), skipped)
    return zones
