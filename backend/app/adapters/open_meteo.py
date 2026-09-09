"""Open-Meteo adapters: marine (waves, SST, currents) and weather (wind).

Both endpoints share a request shape, so they share a base class here. Tier 1
in CLAUDE.md's terms: no key, works today, and therefore the source the demo
actually stands on.

---------------------------------------------------------------------------
THREE THINGS THIS API DOES THAT WILL BURN YOU
---------------------------------------------------------------------------

1. UNITS ARE NOT SI BY DEFAULT, AND THE WRONG PARAMETER IS IGNORED IN SILENCE.

   ocean_current_velocity and wind_speed_10m both default to km/h. The
   parameter that fixes BOTH is wind_speed_unit=ms -- yes, wind_speed_unit
   controls ocean current velocity on the marine endpoint.

   The plausible-looking `current_velocity_unit=ms` does NOT exist. Open-Meteo
   does not reject it, does not warn, and returns km/h:

       current_velocity_unit=ms  ->  'km/h'  [2.2, 2.2, 2.2]    WRONG, silent
       wind_speed_unit=ms        ->  'm/s'   [0.61, 0.61, 0.61] correct

   A 3.6x error in current speed with no error anywhere. That is why every
   returned unit string is asserted against an expected value below and why a
   surprise raises instead of being converted: an unrecognised unit means the
   API changed under us, and guessing at that is how the 3.6x gets in.

2. MULTI-POINT RESPONSES ARE ORDERED, BUT location_id IS NOT USABLE.

   Passing comma-separated coordinates returns a JSON *list*. The first
   element has location_id = None and the rest are 1, 2, 3... So keying on
   location_id silently misattributes point 0. We zip on array order, which
   the API does preserve.

3. COORDINATES COME BACK SNAPPED TO THE MODEL GRID.

   Ask for (13.0, 80.5), the marine API answers for (13.041664, 80.45836) and
   the weather API for (12.970123, 80.50909) -- different grids. We store the
   RETURNED coordinates, because that is where the value actually applies. A
   consequence: wind and waves for one AOI point can land in different H3
   cells. That is real, not a bug.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, ClassVar

import httpx

from app.adapters.base import (
    CANONICAL_UNITS,
    IssuedTimeKind,
    Adapter,
    Observation,
    Variable,
)
from app.aoi import Point

log = logging.getLogger(__name__)

# Open-Meteo's unit spellings -> ours. Translation only: no arithmetic happens
# here, because a unit we have to convert is a unit we did not expect, and an
# unexpected unit is a bug to surface rather than paper over.
UNIT_TRANSLATION: dict[str, str] = {
    "m": "m",
    "s": "s",
    "°": "deg",     # degree sign
    "°C": "degC",   # degree sign + C
    "m/s": "m/s",
    "mg/m³": "mg/m3",
    # Recognised but NOT canonical. Listed on purpose: these are Open-Meteo's
    # defaults, so seeing one means a request parameter was dropped or silently
    # ignored, not that the API changed. Naming them lets the canonical-unit
    # check below produce the error that points at the real fix, instead of the
    # "unrecognised unit" error sending someone to look for an API change.
    "km/h": "km/h",
    "°F": "degF",
    "ft": "ft",
}

# Points per HTTP request. The API accepts comma-separated coordinate lists;
# 50 keeps URLs sane and the whole 20-point demo AOI in a single call.
MAX_POINTS_PER_REQUEST = 50

# Concurrent requests. The free tier asks for restraint and we need very few
# calls, so this is deliberately small.
MAX_CONCURRENT_REQUESTS = 4

REQUEST_TIMEOUT_S = 60.0


class _OpenMeteoAdapter(Adapter, abstract=True):
    """Shared request/parse logic. Subclasses supply endpoint + variable map.

    abstract=True: this is a shared base, not a source. It has no source_id of
    its own, and it implements fetch()/normalize() so abc alone would consider
    it concrete.
    """

    #: Full URL of the endpoint.
    endpoint: ClassVar[str]

    #: Open-Meteo's hourly variable name -> our canonical Variable.
    variable_map: ClassVar[dict[str, Variable]]

    def __init__(
        self,
        points: list[Point],
        confidence: float,
        forecast_days: int = 3,
    ) -> None:
        if not points:
            raise ValueError("no points given")
        self.points = points
        # Constant per source for now, read from sources.reliability by the
        # ingest job. Becomes per-observation when fusion.py has something
        # better to say than a prior.
        self.confidence = confidence
        self.forecast_days = forecast_days

    # -- request ------------------------------------------------------------
    def _params(self, chunk: list[Point]) -> dict[str, Any]:
        return {
            "latitude": ",".join(str(p.lat) for p in chunk),
            "longitude": ",".join(str(p.lon) for p in chunk),
            "hourly": ",".join(self.variable_map),
            "forecast_days": self.forecast_days,
            # Unix timestamps, not ISO strings. Open-Meteo's ISO output is
            # NAIVE ('2026-09-10T00:00'), and the Observation contract rejects
            # naive datetimes -- correctly, since misreading one as local time
            # shifts every value by 5h30m here. Integers leave no room for it.
            "timeformat": "unixtime",
            # See note 1 in the module docstring. This one parameter is what
            # makes wind speed AND ocean current velocity come back in m/s.
            "wind_speed_unit": "ms",
            # Explicit rather than trusting a default that could change.
            "temperature_unit": "celsius",
        }

    async def fetch(self) -> dict[str, Any]:
        """Fetch every point, batched and concurrent.

        Returns the raw per-location payloads plus the fetch timestamp. The
        clock is read HERE, in the method that already does I/O, so that
        normalize() stays a pure function of its input.
        """
        chunks = [
            self.points[i : i + MAX_POINTS_PER_REQUEST]
            for i in range(0, len(self.points), MAX_POINTS_PER_REQUEST)
        ]
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:

            async def one(chunk: list[Point]) -> list[dict[str, Any]]:
                async with sem:
                    r = await client.get(self.endpoint, params=self._params(chunk))
                    r.raise_for_status()
                    body = r.json()
                # A single-point request returns an object, a multi-point
                # request returns a list. Normalise that away immediately.
                return body if isinstance(body, list) else [body]

            # fetched_at is taken once for the whole run, not per chunk, so
            # every row from one job run carries one consistent timestamp.
            fetched_at = datetime.now(timezone.utc)
            results = await asyncio.gather(*(one(c) for c in chunks))

        locations = [loc for chunk_result in results for loc in chunk_result]
        if len(locations) != len(self.points):
            raise ValueError(
                f"{self.source_id}: asked for {len(self.points)} locations, got "
                f"{len(locations)} back. Array order is the only way to match "
                f"points to responses (location_id is None for the first), so "
                f"a length mismatch means we cannot attribute data safely."
            )

        return {"fetched_at": fetched_at.isoformat(), "locations": locations}

    # -- translate ----------------------------------------------------------
    def _check_units(self, hourly_units: dict[str, str]) -> None:
        """Assert every returned unit is one we recognise AND the canonical one
        for its variable. Raises rather than converting. See note 1 above.
        """
        for om_name, variable in self.variable_map.items():
            returned = hourly_units.get(om_name)
            if returned is None:
                raise ValueError(
                    f"{self.source_id}: no unit reported for {om_name!r}; "
                    f"cannot trust the values."
                )
            translated = UNIT_TRANSLATION.get(returned)
            if translated is None:
                raise ValueError(
                    f"{self.source_id}: unrecognised unit {returned!r} for "
                    f"{om_name!r}. The API changed. Do NOT add a conversion "
                    f"here without checking what it now returns -- add the "
                    f"spelling to UNIT_TRANSLATION only if it is the same unit."
                )
            expected = CANONICAL_UNITS[variable]
            if translated != expected:
                raise ValueError(
                    f"{self.source_id}: {om_name!r} came back in {returned!r} "
                    f"(= {translated!r}) but {variable.value} is canonically "
                    f"{expected!r}. Check the request parameters -- most likely "
                    f"wind_speed_unit=ms is missing or was silently ignored."
                )

    def normalize(self, raw: dict[str, Any]) -> list[Observation]:
        fetched_at = datetime.fromisoformat(raw["fetched_at"])
        observations: list[Observation] = []

        # Zip on array order. location_id is unusable -- see note 2 above.
        for location in raw["locations"]:
            self._check_units(location["hourly_units"])

            # The snapped grid coordinates, not what we asked for. Note 3.
            lat = float(location["latitude"])
            lon = float(location["longitude"])

            hourly = location["hourly"]
            times = hourly["time"]

            for om_name, variable in self.variable_map.items():
                values = hourly.get(om_name)
                if values is None:
                    continue
                for ts, value in zip(times, values):
                    # Null = no data (land, or outside the model domain).
                    # Dropped, never emitted as NaN: a NaN propagates through
                    # fusion arithmetic and poisons a whole risk cell.
                    if value is None:
                        continue
                    observations.append(
                        Observation(
                            valid_time=datetime.fromtimestamp(ts, tz=timezone.utc),
                            issued_time=fetched_at,
                            # NOT a real model issue time. Open-Meteo publishes
                            # none -- verified by probe: no body field, no
                            # header. This is when WE fetched, which bounds
                            # data age from below only. Tagged so
                            # core/fusion.py can tell it apart from Copernicus
                            # and IMD, which do carry a true run time.
                            issued_time_kind=IssuedTimeKind.FETCH_PROXY,
                            lat=lat,
                            lon=lon,
                            variable=variable,
                            value=float(value),
                            unit=CANONICAL_UNITS[variable],
                            source_id=self.source_id,
                            confidence=self.confidence,
                        )
                    )

        return observations


class OpenMeteoMarineAdapter(_OpenMeteoAdapter):
    """Waves, sea surface temperature and surface currents."""

    source_id = "open_meteo_marine"
    endpoint = "https://marine-api.open-meteo.com/v1/marine"
    variable_map = {
        "wave_height": Variable.WAVE_HEIGHT,
        "wave_period": Variable.WAVE_PERIOD,
        # Direction convention: "from" (meteorological). See base.py.
        "wave_direction": Variable.WAVE_DIRECTION,
        "sea_surface_temperature": Variable.SST,
        "ocean_current_velocity": Variable.CURRENT_SPEED,
        # Direction convention: "to" (oceanographic) -- OPPOSITE to waves and
        # wind above. Open-Meteo documents ocean_current_direction as the
        # direction the current flows toward.
        "ocean_current_direction": Variable.CURRENT_DIRECTION,
    }


class OpenMeteoWeatherAdapter(_OpenMeteoAdapter):
    """Surface wind.

    10 m reference height, which is what Variable.WIND_SPEED means throughout
    ORCA. Open-Meteo also offers 80/120/180 m; mixing heights into one variable
    would make wind speeds incomparable between sources for no visible reason.
    """

    source_id = "open_meteo_wx"
    endpoint = "https://api.open-meteo.com/v1/forecast"
    variable_map = {
        "wind_speed_10m": Variable.WIND_SPEED,
        # Direction convention: "from" (meteorological).
        "wind_direction_10m": Variable.WIND_DIRECTION,
    }
