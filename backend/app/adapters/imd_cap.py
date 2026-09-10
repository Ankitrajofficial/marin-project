"""India Meteorological Department CAP alerts.

THE ONLY LIVE INDIAN GOVERNMENT SOURCE IN ORCA, and the reason this module
exists: the problem statement is ISRO's and every source it names is Indian,
while everything else ORCA ingests is Open-Meteo -- a global model with no
Indian authority behind it.

WHAT WAS VERIFIED, AND WHAT WAS NOT
-----------------------------------
Reachable with no credentials (checked, not assumed):

    https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml    HTTP 200, text/xml

The feed declares <copyright>public domain</copyright>. Individual alerts are
CAP 1.2 with polygons, severity, urgency, certainty and a validity window.
Sibling channels exist in the same bucket: in-imd-ur (Urdu), in-ndma-en and
in-ndma-ur (National Disaster Management Authority).

NOT reachable. api.imd.gov.in/api/v1/* is fully documented at
api.imd.gov.in/public/api_reference.html -- cyclone_track, cyclone_wind,
cyclone_cou, seabulletin, coastalbulletin, portwarning -- and EVERY ONE returns

    HTTP 401 {"error":"API key missing"}

The reference page never mentions authentication. Probing shows `x-api-key` is
the header it recognises (supplying it advances the error to "Authorization
header missing or invalid"), so a key AND an Authorization header are needed.
When ORCA obtains a key, those endpoints are the right home for real cyclone
track and cone-of-uncertainty data, and they belong in a sibling module. Until
then this file does not pretend to have them.

WHY THESE ARE ZONES AND NOT OBSERVATIONS
----------------------------------------
A CAP alert is a POLYGON with a CATEGORICAL severity and a validity window. It
is not a scalar measurement of anything, so it cannot go through the
Observation contract without inventing a number and a unit -- and the whole
point of that contract is that it refuses invented numbers. Same reasoning
that put AIS positions in vessel_positions and maritime boundaries in
hazard_zones: same rigour, different shape.

They land in hazard_zones, which is what core/geofence.py already reads, so an
IMD warning polygon becomes something a vessel can be inside or near with no
change to core/.

AUTHORITY -- THE FIRST 'official' ROWS IN THE DATABASE
------------------------------------------------------
Every other zone in ORCA is authority='open_data_advisory': MarineRegions is a
VLIZ compilation, OSM is crowd-sourced, neither carries legal weight. IMD is
different. It is India's legally mandated national meteorological authority, so
these rows are authority='official' -- the first in the system -- and
reliability is 0.98, higher than anything else, because this is not a model
guess but the warning the state actually issued.

That makes attribution non-negotiable rather than polite. IMD requires it, the
attribution column is NOT NULL, and it is carried through the geofence API to
the UI.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from app.zones.base import AUTHORITY_OFFICIAL, ZoneRecord

log = logging.getLogger(__name__)

SOURCE_ID = "imd_cap"
RSS_URL = "https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml"
TIMEOUT_S = 90.0

#: zone_type for an IMD warning polygon. Deliberately NOT in routing's
#: blocking sets: a warning is information, not a prohibition. A boat may
#: legally sail into a warned area, and routing must not silently forbid it
#: the way it forbids a treaty boundary.
ZONE_IMD_WARNING = "imd_warning"

CAP_NS = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}

#: IMD requires explicit attribution. This string is written to every row and
#: surfaced by the API; the NOT NULL column means it cannot be dropped.
ATTRIBUTION = (
    "Warning issued by the India Meteorological Department (IMD), "
    "Ministry of Earth Sciences, Government of India. Distributed as CAP via "
    "the WMO Alert Hub. Source: mausam.imd.gov.in"
)
LICENSE = "Public domain (as declared by the IMD CAP feed)"

#: CAP severity -> a rough ordering, stored so a consumer can sort without
#: parsing words. NOT converted into a hazard probability: CAP severity is a
#: category assigned by a forecaster, and mapping it onto the probability
#: scale core/risk.py computes would be inventing a number.
SEVERITY_RANK = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1,
                 "Unknown": 0}


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.find(path, CAP_NS)
    return (found.text or "").strip() if found is not None and found.text else None


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _polygon(raw: str) -> dict[str, Any] | None:
    """CAP polygon -> GeoJSON.

    CAP writes "lat,lon lat,lon ..." -- LATITUDE FIRST. GeoJSON is [lon, lat].
    Getting this backwards puts an Odisha rainfall warning in the Indian Ocean
    off Somalia and still renders as a valid polygon. Third source in this
    codebase with its own axis order, after the MarineRegions WFS (lon-first)
    and Overpass (lat-first).
    """
    ring: list[list[float]] = []
    for pair in raw.split():
        try:
            lat_s, lon_s = pair.split(",")[:2]
            lat, lon = float(lat_s), float(lon_s)
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            ring.append([lon, lat])
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


async def fetch_alerts(limit: int = 40) -> list[dict[str, Any]]:
    """RSS index -> raw CAP documents. Returns (url, xml) pairs."""
    headers = {"User-Agent": "ORCA/0.1 (SIH 2026 marine hazard engine)"}
    async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers,
                                 follow_redirects=True) as client:
        r = await client.get(RSS_URL)
        r.raise_for_status()
        rss = ET.fromstring(r.content)
        links = [it.findtext("link", "") for it in rss.iter("item")]
        links = [u for u in links if u][:limit]

        out = []
        for url in links:
            try:
                d = await client.get(url)
                d.raise_for_status()
                out.append({"url": url, "xml": d.content})
            except httpx.HTTPError as e:
                # One bad document must not lose the rest of the feed.
                log.warning("imd_cap: could not fetch %s: %s", url, e)
    log.info("imd_cap: %d alert document(s) fetched from %d feed item(s)",
             len(out), len(links))
    return out


def normalize(raw: list[dict[str, Any]]) -> list[ZoneRecord]:
    """CAP documents -> ZoneRecords. Pure; no I/O, no clock.

    Alerts WITHOUT a polygon are skipped with a warning, not stored with a
    guessed area. CAP also allows geocodes (state/district codes) instead of
    geometry, and resolving those needs an administrative boundary set we do
    not have -- inventing a polygon for a warning would put a government
    warning in the wrong place, which is worse than not showing it.
    """
    zones: list[ZoneRecord] = []
    skipped_no_geom = 0

    for doc in raw:
        try:
            alert = ET.fromstring(doc["xml"])
        except ET.ParseError as e:
            log.warning("imd_cap: unparseable CAP document %s: %s", doc["url"], e)
            continue

        identifier = _text(alert, "c:identifier") or doc["url"]
        sent = _parse_time(_text(alert, "c:sent"))
        status = _text(alert, "c:status")
        msg_type = _text(alert, "c:msgType")

        # Only live public alerts. An Exercise or Test message must never
        # reach an operational display.
        if status and status not in ("Actual",):
            log.info("imd_cap: skipping status=%s (%s)", status, identifier)
            continue

        for i, info in enumerate(alert.findall("c:info", CAP_NS)):
            event = _text(info, "c:event") or "IMD warning"
            severity = _text(info, "c:severity") or "Unknown"
            headline = _text(info, "c:headline")
            onset = _parse_time(_text(info, "c:onset")) or sent
            expires = _parse_time(_text(info, "c:expires"))

            params = {_text(p, "c:valueName"): _text(p, "c:value")
                      for p in info.findall("c:parameter", CAP_NS)}

            for j, area in enumerate(info.findall("c:area", CAP_NS)):
                area_desc = _text(area, "c:areaDesc") or "unspecified area"
                poly_el = area.find("c:polygon", CAP_NS)
                if poly_el is None or not (poly_el.text or "").strip():
                    skipped_no_geom += 1
                    log.warning(
                        "imd_cap: alert %s area %r has no polygon (CAP geocode "
                        "only); SKIPPED rather than stored with a guessed "
                        "boundary", identifier, area_desc[:60])
                    continue
                geometry = _polygon(poly_el.text or "")
                if geometry is None:
                    skipped_no_geom += 1
                    continue

                zones.append(ZoneRecord(
                    zone_id=f"imd_cap:{identifier}:{i}:{j}",
                    zone_type=ZONE_IMD_WARNING,
                    name=f"{event} — {area_desc}",
                    geometry=geometry,
                    # The first 'official' rows in this database.
                    authority=AUTHORITY_OFFICIAL,
                    attribution=ATTRIBUTION,
                    license=LICENSE,
                    source_id=SOURCE_ID,
                    source_url=doc["url"],
                    meta={
                        "cap_identifier": identifier,
                        "msg_type": msg_type,
                        "status": status,
                        "event": event,
                        "headline": headline,
                        "severity": severity,
                        "severity_rank": SEVERITY_RANK.get(severity, 0),
                        "urgency": _text(info, "c:urgency"),
                        "certainty": _text(info, "c:certainty"),
                        "response_type": _text(info, "c:responseType"),
                        "sender_name": _text(info, "c:senderName"),
                        "description": _text(info, "c:description"),
                        "instruction": _text(info, "c:instruction"),
                        "web": _text(info, "c:web"),
                        "area_desc": area_desc,
                        "cap_parameters": params,
                        "sent": sent.isoformat() if sent else None,
                        "onset": onset.isoformat() if onset else None,
                        "expires": expires.isoformat() if expires else None,
                    },
                    valid_from=onset,
                    valid_until=expires,
                ))

    log.info("imd_cap: %d warning polygon(s), %d area(s) skipped for lack of "
             "geometry", len(zones), skipped_no_geom)
    return zones


async def fetch_all() -> list[ZoneRecord]:
    return normalize(await fetch_alerts())
