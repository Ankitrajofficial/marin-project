"""OSM Overpass loader: harbours, fishing harbours and marinas.

Recall triage is meaningless without destinations -- "get to safety" needs a
place. This supplies them.

WHAT OSM DOES NOT GIVE US, AND WHY IT MATTERS
---------------------------------------------
No usable depth. Probed across the whole AOI: 103 harbour features, 57 named,
and ZERO carrying depth, seamark:harbour:depth or maxdraft. So
harbours.depth_source is 'unknown' for effectively every row, and
core/recall.py must treat that as "we cannot verify this vessel fits", not as
"it fits".

This is deliberately NOT patched with GEBCO. GEBCO is seabed bathymetry on a
~450 m grid; a harbour's usable depth is a dredged and maintained channel
figure that GEBCO cannot observe. A GEBCO sounding outside a harbour mouth
tells you nothing about the channel inside it, and presenting one as a draft
check would invent a safety guarantee.

Marinas are included on purpose. They are small-craft facilities, and small
craft are exactly who a cyclone recall list is for -- a fishing boat can enter
places a bulk carrier cannot.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

OVERPASS = "https://overpass-api.de/api/interpreter"
SOURCE_ID = "osm_harbours"
TIMEOUT_S = 300.0

# south,west,north,east -- Overpass is lat-first (the MarineRegions WFS in the
# sibling module is lon-first; both are stated where they are used).
DEFAULT_BBOX = (7.0, 72.0, 14.0, 81.5)

_QUERY = """
[out:json][timeout:240];
(
  node["harbour"]({s},{w},{n},{e});
  way["harbour"]({s},{w},{n},{e});
  node["seamark:type"="harbour"]({s},{w},{n},{e});
  way["seamark:type"="harbour"]({s},{w},{n},{e});
  node["leisure"="marina"]({s},{w},{n},{e});
  way["leisure"="marina"]({s},{w},{n},{e});
  way["landuse"="harbour"]({s},{w},{n},{e});
  node["man_made"="port"]({s},{w},{n},{e});
);
out tags center;
"""

# Any of these tags carrying a number would be a real depth. None of them
# appear in our AOI today -- kept so the loader picks them up the moment a
# mapper adds one, rather than silently ignoring it.
_DEPTH_KEYS = ("seamark:harbour:depth", "depth", "maxdraft", "draft",
               "seamark:depth_area:minimum_depth")


def _classify(tags: dict[str, str]) -> str:
    cat = (tags.get("seamark:harbour:category") or tags.get("harbour:category")
           or tags.get("harbour") or "")
    blob = f"{cat} {tags.get('name', '')}".lower()
    if "fish" in blob:
        return "fishing"
    if "marina" in blob or tags.get("leisure") == "marina":
        return "marina"
    if "shipyard" in blob or "repair" in blob:
        return "shipyard"
    if tags.get("harbour") or tags.get("man_made") == "port" or "port" in blob:
        return "port"
    return "unknown"


def _depth(tags: dict[str, str]) -> tuple[float | None, str]:
    for k in _DEPTH_KEYS:
        raw = tags.get(k)
        if not raw:
            continue
        try:
            return float(str(raw).split()[0]), "osm"
        except (ValueError, IndexError):
            log.warning("harbour depth tag %s=%r is not a number; treating as "
                        "unknown rather than guessing", k, raw)
    return None, "unknown"


async def fetch_all(bbox: tuple[float, float, float, float] = DEFAULT_BBOX
                    ) -> list[dict[str, Any]]:
    s, w, n, e = bbox
    headers = {"User-Agent": "ORCA/0.1 (SIH 2026 marine hazard engine)",
               "Content-Type": "text/plain; charset=utf-8"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers) as client:
        r = await client.post(
            OVERPASS, content=_QUERY.format(s=s, w=w, n=n, e=e).encode("utf-8")
        )
        r.raise_for_status()
        body = r.json()

    out, unnamed = [], 0
    for el in body.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            # An unnamed harbour cannot be communicated to a skipper over the
            # radio, which is the whole point of the output.
            unnamed += 1
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue

        depth_m, depth_source = _depth(tags)
        out.append({
            "harbour_id": f"osm:{el['type']}:{el['id']}",
            "name": name,
            "lat": float(lat), "lon": float(lon),
            "depth_m": depth_m, "depth_source": depth_source,
            "harbour_type": _classify(tags),
            "capacity": None,
            "source_id": SOURCE_ID,
            "source_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "meta": {k: v for k, v in tags.items()
                     if k.startswith("seamark") or k in
                     ("harbour", "leisure", "landuse", "man_made", "operator")},
        })

    log.info("osm_harbours: %d named harbours, %d unnamed skipped, %d with a "
             "real depth tag", len(out), unnamed,
             sum(1 for h in out if h["depth_source"] == "osm"))
    return out


_UPSERT = """
INSERT INTO harbours (harbour_id, name, lat, lon, depth_m, depth_source,
                      harbour_type, capacity, source_id, source_url, meta,
                      fetched_at)
VALUES (%(harbour_id)s, %(name)s, %(lat)s, %(lon)s, %(depth_m)s,
        %(depth_source)s, %(harbour_type)s, %(capacity)s, %(source_id)s,
        %(source_url)s, %(meta)s, now())
ON CONFLICT (harbour_id) DO UPDATE SET
    name = EXCLUDED.name, lat = EXCLUDED.lat, lon = EXCLUDED.lon,
    depth_m = EXCLUDED.depth_m, depth_source = EXCLUDED.depth_source,
    harbour_type = EXCLUDED.harbour_type, source_url = EXCLUDED.source_url,
    meta = EXCLUDED.meta, fetched_at = now()
"""


async def write_harbours(conn: Any, rows: list[dict[str, Any]]) -> int:
    from psycopg.types.json import Json
    if not rows:
        return 0
    async with conn.cursor() as cur:
        for r in rows:
            await cur.execute(_UPSERT, dict(r, meta=Json(r["meta"])))
    return len(rows)
