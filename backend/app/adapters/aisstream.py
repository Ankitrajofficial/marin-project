"""AISStream.io live vessel positions.

NOT an adapters.base.Adapter, on purpose. That ABC guarantees everything
reaching `observations` passed the Observation contract -- one value, one
place, one time, one canonical unit. An AIS position report is a different
shape entirely (a vessel state, not a measurement of the sea) and lands in
`vessel_positions`. Forcing it through the Observation contract would dilute
the one invariant holding the data layer together. Same discipline, different
table: units are converted at the boundary, nothing is guessed, and gaps are
recorded rather than papered over.

FOUR THINGS ABOUT THIS FEED THAT WILL BURN YOU
----------------------------------------------
1. SOG IS IN KNOTS. Our schema is SI. 12 kn stored as 12 m/s is a boat moving
   at 23 knots, and every time-to-harbour computed from it is optimistic by a
   factor of two -- the dangerous direction for a recall list. Converted here,
   once, at the boundary.

2. BOUNDING BOXES ARE [[lat, lon], [lat, lon]] -- LAT FIRST. The opposite of
   the MarineRegions WFS in zones/. A swapped box silently subscribes to
   nothing and looks exactly like "no traffic today".

3. time_utc IS A Go TIMESTAMP, not ISO 8601: "2026-09-10 05:00:00.123 +0000
   UTC". datetime.fromisoformat() rejects it. Parsed explicitly below, with a
   fallback to the receive time that is FLAGGED rather than silent.

4. DRAUGHT ARRIVES ON A DIFFERENT MESSAGE TYPE than position. A vessel can be
   tracked for hours with no draft_m at all, because ShipStaticData is
   broadcast every 6 minutes at best and often not at all. core/recall.py must
   handle draft=NULL as "cannot verify fit", never as "fits".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import websockets

from app.config import settings

log = logging.getLogger(__name__)

SOURCE_ID = "aisstream"
WS_URL = "wss://stream.aisstream.io/v0/stream"

KNOTS_TO_MS = 0.514444

#: south, west, north, east -- our AOI.
DEFAULT_BBOX = (7.0, 72.0, 14.0, 81.5)

#: AIS sentinel values meaning "not available".
SOG_UNAVAILABLE = 102.3      # knots
COG_UNAVAILABLE = 360.0

#: Buffer before writing. AIS is chatty; one INSERT per report would spend the
#: whole budget on round trips.
FLUSH_EVERY = 50
FLUSH_SECONDS = 5.0

#: Reconnect backoff.
BACKOFF_BASE_S = 2.0
BACKOFF_MAX_S = 60.0

# AIS ship type code -> (class, default transit speed m/s).
#
# The default is used by core/recall.py only when a vessel has too few observed
# SOG samples. Every default is deliberately at the SLOW end of plausible: a
# slower assumed speed means a longer time-to-harbour, a smaller margin, and a
# HIGHER recall priority. If the guess is wrong, it is wrong toward calling the
# boat in early.
_SHIP_TYPES: tuple[tuple[range, str, float], ...] = (
    (range(30, 31), "fishing", 3.6),
    (range(31, 33), "towing", 3.6),
    (range(35, 36), "military", 6.0),
    (range(36, 37), "sailing", 2.6),
    (range(37, 38), "pleasure", 3.1),
    (range(40, 50), "high_speed_craft", 10.0),
    (range(50, 60), "special", 4.1),
    (range(60, 70), "passenger", 7.2),
    (range(70, 80), "cargo", 6.2),
    (range(80, 90), "tanker", 5.7),
)
UNKNOWN_CLASS = "unknown"
UNKNOWN_SPEED_MS = 3.1       # ~6 kn, conservative on purpose


def classify(type_code: int | None) -> tuple[str, float]:
    if type_code is not None:
        for rng, name, speed in _SHIP_TYPES:
            if type_code in rng:
                return name, speed
    return UNKNOWN_CLASS, UNKNOWN_SPEED_MS


def parse_go_time(raw: str | None) -> datetime | None:
    """Parse AISStream's Go-formatted timestamp.

    '2026-09-10 05:00:00.123456789 +0000 UTC'. Not ISO, and Python's parser
    chokes on both the 9-digit fraction and the trailing ' UTC'.
    """
    if not raw:
        return None
    txt = raw.strip().removesuffix(" UTC").strip()
    date_part, _, rest = txt.partition(" ")
    time_part, _, offset = rest.partition(" ")
    if "." in time_part:                       # trim ns -> us
        head, frac = time_part.split(".", 1)
        time_part = f"{head}.{frac[:6]}"
    try:
        dt = datetime.fromisoformat(f"{date_part} {time_part}")
    except ValueError:
        return None
    if offset and offset not in ("+0000", "UTC"):
        try:
            sign = 1 if offset[0] == "+" else -1
            hh, mm = int(offset[1:3]), int(offset[3:5])
            from datetime import timedelta
            return dt.replace(tzinfo=timezone(sign * timedelta(hours=hh, minutes=mm)))
        except (ValueError, IndexError):
            pass
    return dt.replace(tzinfo=timezone.utc)


@dataclass
class Position:
    mmsi: str
    ts: datetime
    lat: float
    lon: float
    sog: float | None            # m/s (SI) -- converted from knots
    cog: float | None            # degrees true
    nav_status: int | None
    ts_is_receive_time: bool     # True when the feed's own timestamp was unusable


@dataclass
class VesselInfo:
    mmsi: str
    name: str | None = None
    vessel_class: str | None = None
    cruise_speed: float | None = None
    draft_m: float | None = None


@dataclass
class Stats:
    messages: int = 0
    positions: int = 0
    statics: int = 0
    unparsed: int = 0
    reconnects: int = 0
    vessels: set[str] = field(default_factory=set)
    started: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_position(msg: dict[str, Any], received: datetime) -> Position | None:
    meta = msg.get("MetaData") or {}
    body = (msg.get("Message") or {}).get("PositionReport") or {}
    mmsi = str(meta.get("MMSI") or body.get("UserID") or "").strip()
    if not mmsi:
        return None

    lat = _f(body.get("Latitude", meta.get("latitude")))
    lon = _f(body.get("Longitude", meta.get("longitude")))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    sog_kn = _f(body.get("Sog"))
    # 102.3 kn is the AIS "not available" sentinel, not a speedboat.
    sog = (sog_kn * KNOTS_TO_MS
           if sog_kn is not None and sog_kn < SOG_UNAVAILABLE else None)

    cog = _f(body.get("Cog"))
    if cog is not None and cog >= COG_UNAVAILABLE:
        cog = None

    ts = parse_go_time(meta.get("time_utc"))
    return Position(
        mmsi=mmsi, ts=ts or received, lat=lat, lon=lon, sog=sog, cog=cog,
        nav_status=body.get("NavigationalStatus"),
        ts_is_receive_time=ts is None,
    )


def parse_static(msg: dict[str, Any]) -> VesselInfo | None:
    meta = msg.get("MetaData") or {}
    body = (msg.get("Message") or {}).get("ShipStaticData") or {}
    mmsi = str(meta.get("MMSI") or body.get("UserID") or "").strip()
    if not mmsi:
        return None

    name = (body.get("Name") or meta.get("ShipName") or "").strip() or None
    vessel_class, default_speed = classify(body.get("Type"))
    draft = _f(body.get("MaximumStaticDraught"))
    # AIS encodes "unknown" draught as 0. A vessel with zero draught does not
    # float; treating it as a real value would let any harbour look compatible.
    if draft is not None and draft <= 0:
        draft = None

    return VesselInfo(mmsi=mmsi, name=name, vessel_class=vessel_class,
                      cruise_speed=default_speed, draft_m=draft)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
_POS_SQL = """
INSERT INTO vessel_positions (ts, mmsi, lat, lon, sog, cog, h3_cell)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (mmsi, ts) DO UPDATE SET
    lat = EXCLUDED.lat, lon = EXCLUDED.lon, sog = EXCLUDED.sog,
    cog = EXCLUDED.cog, h3_cell = EXCLUDED.h3_cell
"""

# Unknown vessels are KEPT, never dropped -- the reason vessel_positions has no
# foreign key to vessels. A boat we have never seen before is exactly the boat
# a recall list must not lose. Static details fill in later if they ever
# arrive; COALESCE means a later NULL never erases what we already learned.
_VESSEL_SQL = """
INSERT INTO vessels (mmsi, name, vessel_class, cruise_speed, draft_m)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (mmsi) DO UPDATE SET
    name         = COALESCE(EXCLUDED.name, vessels.name),
    vessel_class = COALESCE(EXCLUDED.vessel_class, vessels.vessel_class),
    cruise_speed = COALESCE(EXCLUDED.cruise_speed, vessels.cruise_speed),
    draft_m      = COALESCE(EXCLUDED.draft_m, vessels.draft_m)
"""

_GAP_OPEN = """
INSERT INTO ingest_gaps (source_id, started_at, reason, messages_before)
VALUES (%s, %s, %s, %s) RETURNING gap_id
"""
# Closes EVERY open gap for the source, not just the one this process opened.
# A gap opened by a process that then dies would otherwise stay open forever --
# and a crash is precisely when a gap gets opened. "We are listening now" means
# no gap is open, regardless of who recorded it.
_GAP_CLOSE = """
UPDATE ingest_gaps SET ended_at = %s
 WHERE source_id = %s AND ended_at IS NULL
RETURNING gap_id
"""


class AISStreamIngest:
    """Long-lived subscriber. Reconnects, and records every gap it leaves."""

    def __init__(self, bbox: tuple[float, float, float, float] = DEFAULT_BBOX,
                 api_key: str | None = None) -> None:
        self.bbox = bbox
        self.api_key = api_key or settings.aisstream_api_key
        if not self.api_key:
            raise RuntimeError(
                "AISSTREAM_API_KEY is not set. Add it to backend/.env."
            )
        self.stats = Stats()
        self._buffer: list[Position] = []
        self._vessels: dict[str, VesselInfo] = {}
        self._open_gap_id: int | None = None

    def subscription(self) -> dict[str, Any]:
        s, w, n, e = self.bbox
        return {
            "APIKey": self.api_key,
            # LAT FIRST. See note 2 in the module docstring.
            "BoundingBoxes": [[[s, w], [n, e]]],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }

    # -- gaps ------------------------------------------------------------
    async def _open_gap(self, conn: Any, when: datetime, reason: str) -> None:
        async with conn.cursor() as cur:
            await cur.execute(_GAP_OPEN, (SOURCE_ID, when, reason[:400],
                                          self.stats.messages))
            self._open_gap_id = (await cur.fetchone())[0]
        log.warning("ais gap opened (id=%s): %s", self._open_gap_id, reason[:160])

    async def _close_gap(self, conn: Any, when: datetime) -> None:
        """Close any open gap for this source. Called on every successful
        connect, including the first -- which is what recovers a gap left
        behind by a previous process that crashed."""
        async with conn.cursor() as cur:
            await cur.execute(_GAP_CLOSE, (when, SOURCE_ID))
            closed = [r[0] for r in await cur.fetchall()]
        if closed:
            log.info("ais gap(s) closed: %s", closed)
        self._open_gap_id = None

    # -- writing ---------------------------------------------------------
    async def flush(self, conn: Any) -> int:
        from app.core.grid import cell_for

        if not self._buffer and not self._vessels:
            return 0
        positions, self._buffer = self._buffer, []
        vessels, self._vessels = self._vessels, {}

        async with conn.cursor() as cur:
            if vessels:
                await cur.executemany(_VESSEL_SQL, [
                    (v.mmsi, v.name, v.vessel_class, v.cruise_speed, v.draft_m)
                    for v in vessels.values()
                ])
            if positions:
                # Seeing an MMSI at all is enough to register the vessel, even
                # with no static message: an unidentified boat still needs
                # rescuing.
                await cur.executemany(_VESSEL_SQL, [
                    (p.mmsi, None, None, None, None) for p in positions
                ])
                await cur.executemany(_POS_SQL, [
                    (p.ts, p.mmsi, p.lat, p.lon, p.sog, p.cog,
                     cell_for(p.lat, p.lon))
                    for p in positions
                ])
        return len(positions)

    # -- main loop -------------------------------------------------------
    async def run(self, get_conn, duration_s: float | None = None) -> Stats:
        """Subscribe and persist until `duration_s` elapses (None = forever)."""
        deadline = (asyncio.get_running_loop().time() + duration_s
                    if duration_s else None)
        attempt = 0

        while deadline is None or asyncio.get_running_loop().time() < deadline:
            try:
                async with websockets.connect(WS_URL, ping_interval=20,
                                              ping_timeout=20) as ws:
                    await ws.send(json.dumps(self.subscription()))
                    log.info("ais subscribed bbox=%s", self.bbox)
                    async with get_conn() as conn:
                        await self._close_gap(conn, datetime.now(timezone.utc))
                    attempt = 0
                    await self._consume(ws, get_conn, deadline)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # EVERY disconnect opens a gap row. A silent reconnect would
                # leave a hole in the track that looks identical to a vessel
                # that stopped transmitting -- and those mean opposite things.
                async with get_conn() as conn:
                    await self.flush(conn)
                    if self._open_gap_id is None:
                        await self._open_gap(conn, datetime.now(timezone.utc),
                                             f"{type(e).__name__}: {e}")
                self.stats.reconnects += 1
                if deadline and asyncio.get_running_loop().time() >= deadline:
                    break
                delay = min(BACKOFF_BASE_S * (2 ** attempt), BACKOFF_MAX_S)
                delay *= 0.6 + 0.8 * random.random()
                log.warning("ais disconnected (%s); reconnecting in %.1fs",
                            type(e).__name__, delay)
                await asyncio.sleep(delay)
                attempt += 1

        async with get_conn() as conn:
            await self.flush(conn)
        return self.stats

    async def _consume(self, ws, get_conn, deadline) -> None:
        last_flush = asyncio.get_running_loop().time()
        while True:
            now_mono = asyncio.get_running_loop().time()
            if deadline and now_mono >= deadline:
                async with get_conn() as conn:
                    await self.flush(conn)
                return

            timeout = 1.0 if deadline is None else max(0.1, min(1.0, deadline - now_mono))
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            except asyncio.TimeoutError:
                raw = None

            if raw is not None:
                received = datetime.now(timezone.utc)
                self.stats.messages += 1
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    self.stats.unparsed += 1
                    continue

                kind = msg.get("MessageType")
                if kind == "PositionReport":
                    p = parse_position(msg, received)
                    if p:
                        self._buffer.append(p)
                        self.stats.positions += 1
                        self.stats.vessels.add(p.mmsi)
                    else:
                        self.stats.unparsed += 1
                elif kind == "ShipStaticData":
                    v = parse_static(msg)
                    if v:
                        self._vessels[v.mmsi] = v
                        self.stats.statics += 1
                        self.stats.vessels.add(v.mmsi)
                elif msg.get("error"):
                    raise RuntimeError(f"aisstream error: {msg['error']}")

            elapsed = asyncio.get_running_loop().time() - last_flush
            if len(self._buffer) >= FLUSH_EVERY or elapsed >= FLUSH_SECONDS:
                async with get_conn() as conn:
                    await self.flush(conn)
                last_flush = asyncio.get_running_loop().time()
