"""GET /api/geofence and GET /api/zones.

Every response carries advisory_only and the attributions, both required
fields on the response models. See app/core/geofence.py for why the numbers
are computed the way they are.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import (
    GeofenceResponse,
    ZoneFeature,
    ZoneFeatureCollection,
    ZoneHitOut,
    ZoneProperties,
)
from app.core.geofence import DEFAULT_BUFFER_NM, proximity_alert
from app.db import get_conn

router = APIRouter(prefix="/api", tags=["geofence"])

BOUNDARY_DISCLAIMER = (
    "Boundaries shown are from open datasets (MarineRegions/VLIZ, OpenStreetMap) "
    "and are ADVISORY ONLY. They are not Survey of India definitions and carry no "
    "legal authority. Do not rely on them for navigation, enforcement, or any "
    "determination of maritime jurisdiction."
)
OFFICIAL_NOTE = (
    " Warnings marked official are issued by the India Meteorological "
    "Department (IMD), Ministry of Earth Sciences, Government of India, and "
    "are authoritative. Attribution to IMD is required wherever they are shown."
)
DISCLAIMER = BOUNDARY_DISCLAIMER


def _hit(h) -> ZoneHitOut:
    return ZoneHitOut(**asdict(h))


@router.get("/geofence", response_model=GeofenceResponse)
async def geofence(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    buffer_nm: float = Query(DEFAULT_BUFFER_NM, gt=0, le=200,
                             description="alert radius in nautical miles"),
) -> GeofenceResponse:
    async with get_conn() as conn:
        # No try/except: a database failure must surface as a 500, never as an
        # empty result that a client would render as "clear water".
        r = await proximity_alert(conn, lat, lon, buffer_nm)

    return GeofenceResponse(
        lat=r.lat, lon=r.lon, buffer_nm=r.buffer_nm, verdict=r.verdict,
        inside=[_hit(h) for h in r.inside],
        alerts=[_hit(h) for h in r.alerts],
        nearest_by_type={k: _hit(v) for k, v in r.nearest_by_type.items()},
        advisory_only=r.advisory_only,
        boundaries_advisory_only=r.boundaries_advisory_only,
        official_alerts=r.official_alerts,
        attributions=r.attributions,
        # The boundary disclaimer always applies; the official note is added
        # only when an authoritative warning actually bears on the position.
        disclaimer=BOUNDARY_DISCLAIMER + (OFFICIAL_NOTE if r.official_alerts else ""),
    )


@router.get("/zones", response_model=ZoneFeatureCollection)
async def zones(
    bbox: str | None = Query(None, description="west,south,east,north"),
    zone_type: str | None = Query(None),
    simplify_deg: float = Query(
        0.002, ge=0, le=1,
        description="Douglas-Peucker tolerance in degrees, for map rendering only",
    ),
) -> ZoneFeatureCollection:
    # Same rule as the geofence: expired warnings are not current information.
    where, params = ["(valid_until IS NULL OR valid_until > now())"], {}
    if bbox:
        try:
            w, s, e, n = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be west,south,east,north")
        where.append(
            "ST_Intersects(geom, ST_MakeEnvelope(%(w)s, %(s)s, %(e)s, %(n)s, 4326))"
        )
        params |= {"w": w, "s": s, "e": e, "n": n}
    if zone_type:
        where.append("zone_type = %(zt)s")
        params["zt"] = zone_type

    # Simplify for rendering only. India's EEZ is ~1.8 MB of raw coordinates,
    # which is a slow map for no visual gain. ST_SimplifyPreserveTopology keeps
    # rings valid. The geofence NEVER reads this -- it measures against the
    # full-precision geometry, so nothing that affects a verdict is simplified.
    sql = f"""
        SELECT zone_id, zone_type, name, authority, attribution, meta,
               ST_AsGeoJSON(
                   CASE WHEN %(tol)s > 0
                        THEN ST_SimplifyPreserveTopology(geom, %(tol)s)
                        ELSE geom END
               ) AS gj
          FROM hazard_zones
         WHERE {' AND '.join(where)}
         ORDER BY zone_type, zone_id
    """
    params["tol"] = simplify_deg

    import json as _json

    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()

    features = [
        ZoneFeature(
            geometry=_json.loads(gj),
            properties=ZoneProperties(
                zone_id=zid, zone_type=zt, name=name,
                authority=auth, attribution=attr, meta=meta or {},
            ),
        )
        for zid, zt, name, auth, attr, meta, gj in rows
        if gj
    ]

    return ZoneFeatureCollection(
        n_features=len(features),
        advisory_only=all(f.properties.authority != "official" for f in features),
        attributions=sorted({f.properties.attribution for f in features}),
        disclaimer=DISCLAIMER,
        features=features,
    )
