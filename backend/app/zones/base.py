"""Zone records: static geographic boundaries, not observations.

Deliberately NOT an adapters.Adapter. That ABC exists to guarantee everything
reaching `observations` passed the Observation contract -- one value, one
place, one time, one canonical unit. A maritime boundary is none of those
things, and forcing polygons through that contract would dilute the one
invariant currently holding the data layer together.

Same rigour, different shape: every zone must declare where it came from and
whether it carries legal authority, and neither is defaultable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# authority values. See the long comment on hazard_zones in db/02_schema.sql.
#
# Everything ORCA currently loads is ADVISORY. MarineRegions is a VLIZ
# scientific compilation; OSM protected areas are crowd-sourced. Neither is
# Survey of India, and India regulates depiction of its own boundaries.
AUTHORITY_OFFICIAL = "official"
AUTHORITY_ADVISORY = "open_data_advisory"

# zone_type vocabulary.
ZONE_IMBL = "imbl"                         # negotiated maritime boundary (LINE)
ZONE_EEZ = "eez"                           # 200 NM exclusive economic zone
ZONE_TERRITORIAL_SEA = "territorial_sea"   # 12 NM
ZONE_CONTIGUOUS = "contiguous_zone"        # 24 NM
ZONE_BASELINE = "baseline"                 # straight baseline (LINE)
ZONE_MPA = "mpa"                           # marine protected area

_POLYGON_TYPES = {"Polygon", "MultiPolygon"}
_LINE_TYPES = {"LineString", "MultiLineString"}


@dataclass(frozen=True, slots=True)
class ZoneRecord:
    zone_id: str
    zone_type: str
    name: str | None
    geometry: dict[str, Any]      # GeoJSON geometry
    authority: str
    attribution: str              # must be displayed wherever this is shown
    license: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    #: Validity window. NULL/None means "always in force" -- a maritime
    #: boundary does not expire. An IMD warning does, and serving an expired
    #: warning as current is its own kind of wrong answer.
    valid_from: Any = None
    valid_until: Any = None

    def __post_init__(self) -> None:
        if self.authority not in (AUTHORITY_OFFICIAL, AUTHORITY_ADVISORY):
            raise ValueError(f"bad authority {self.authority!r}")
        if not self.attribution:
            raise ValueError(
                f"zone {self.zone_id!r} has no attribution. Every zone must "
                f"carry one -- it is displayed with the boundary, and an "
                f"unattributed boundary is one nobody can check."
            )
        gtype = self.geometry.get("type")
        if gtype not in _POLYGON_TYPES | _LINE_TYPES:
            raise ValueError(f"zone {self.zone_id!r}: unusable geometry {gtype!r}")

    @property
    def collection_dim(self) -> int:
        """ST_CollectionExtract dimension: 2 = lines, 3 = polygons.

        ST_MakeValid can turn a broken polygon into a GeometryCollection with
        stray points and slivers. Extracting the dimension we asked for keeps
        the result inside the column's CHECK constraint instead of failing a
        whole batch on one bad OSM relation.
        """
        return 3 if self.geometry["type"] in _POLYGON_TYPES else 2


_UPSERT_SQL = """
INSERT INTO hazard_zones
    (zone_id, zone_type, name, geom, authority, attribution,
     license, source_id, source_url, meta, valid_from, valid_until, fetched_at)
VALUES (
    %s, %s, %s,
    ST_Multi(ST_CollectionExtract(
        ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s)),
    %s, %s, %s, %s, %s, %s, %s, %s, now()
)
ON CONFLICT (zone_id) DO UPDATE SET
    zone_type   = EXCLUDED.zone_type,
    name        = EXCLUDED.name,
    geom        = EXCLUDED.geom,
    authority   = EXCLUDED.authority,
    attribution = EXCLUDED.attribution,
    license     = EXCLUDED.license,
    source_id   = EXCLUDED.source_id,
    source_url  = EXCLUDED.source_url,
    meta        = EXCLUDED.meta,
    valid_from  = EXCLUDED.valid_from,
    valid_until = EXCLUDED.valid_until,
    fetched_at  = now()
"""


async def write_zones(conn: Any, zones: Sequence[ZoneRecord]) -> int:
    """Upsert zone records. Takes a connection -- see core.grid.fetch_observations."""
    from psycopg.types.json import Json

    if not zones:
        log.warning("write_zones: nothing to write")
        return 0

    async with conn.cursor() as cur:
        for z in zones:
            await cur.execute(
                _UPSERT_SQL,
                (
                    z.zone_id, z.zone_type, z.name,
                    json.dumps(z.geometry), z.collection_dim,
                    z.authority, z.attribution, z.license,
                    z.source_id, z.source_url, Json(z.meta),
                    z.valid_from, z.valid_until,
                ),
            )
    return len(zones)
