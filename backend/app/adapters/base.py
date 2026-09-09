"""The Observation contract.

Every adapter in this package translates one upstream source into a list of
Observation objects and nothing else. No computation, no fusion, no
interpolation, no unit guessing at read time -- an adapter either produces a
valid Observation or raises.

The contract is enforced here rather than documented, because the single most
expensive class of bug in a multi-source pipeline is a silent unit or
convention mismatch: a wave height in feet, a temperature in kelvin, a current
direction pointing the wrong way. Those do not crash. They produce a plausible
wrong number, and by the time it reaches core/risk.py there is no way to tell
it apart from a real one. So every one of them is a hard failure at ingest.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar

import h3
from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

from app.config import H3_RESOLUTION
from app.db import get_conn

log = logging.getLogger(__name__)

# Rows per executemany batch. COPY would be faster but cannot upsert without a
# staging table, and idempotent re-ingest matters more than raw throughput at
# MVP volumes.
INSERT_BATCH_SIZE = 1000


class IssuedTimeKind(str, Enum):
    """What an Observation's issued_time actually MEANS.

    Two sources can both populate issued_time and mean entirely different
    things by it, and the difference is invisible in the value itself:

      MODEL_RUN    the source told us when its model was initialized. The real
                   thing. Copernicus and IMD carry this.

      FETCH_PROXY  the source exposes no run time at all, so this is the
                   wall-clock moment WE retrieved it. It is an UPPER BOUND on
                   data age -- the data is at least this fresh and probably
                   staler, by however long the source sat on it before we
                   asked. Open-Meteo is this: its response carries no
                   initialization time in any body field or header (verified
                   by probe, not assumed).

    This is a column, not a comment, because core/fusion.py compares data-age
    ACROSS sources to arbitrate conflicts. A prose note in one adapter is not
    in scope at the point where that comparison happens; a field is.
    """

    MODEL_RUN = "model_run"
    FETCH_PROXY = "fetch_proxy"


class Variable(str, Enum):
    """Canonical variable names. An adapter maps its source's naming onto
    these; nothing downstream ever sees a source-specific name.
    """

    WAVE_HEIGHT = "wave_height"
    WAVE_PERIOD = "wave_period"
    WAVE_DIRECTION = "wave_direction"
    WIND_SPEED = "wind_speed"
    WIND_DIRECTION = "wind_direction"
    SST = "sst"
    CHL = "chl"
    CURRENT_SPEED = "current_speed"
    CURRENT_DIRECTION = "current_direction"


# ---------------------------------------------------------------------------
# Canonical units. One unit per variable, no alternatives, no conversion at
# read time. If a source disagrees, the ADAPTER converts -- that is what the
# adapter layer is for.
#
# On "SI": these are SI-derived rather than strict SI. sst is degC and chl is
# mg/m^3 because every upstream source emits them that way and every human who
# reads the output thinks in them. Converting to K and kg/m^3 would add a
# failure mode and buy nothing.
CANONICAL_UNITS: dict[Variable, str] = {
    Variable.WAVE_HEIGHT: "m",
    Variable.WAVE_PERIOD: "s",
    Variable.WAVE_DIRECTION: "deg",
    Variable.WIND_SPEED: "m/s",
    Variable.WIND_DIRECTION: "deg",
    Variable.SST: "degC",
    Variable.CHL: "mg/m3",
    Variable.CURRENT_SPEED: "m/s",
    Variable.CURRENT_DIRECTION: "deg",
}

# ---------------------------------------------------------------------------
# Direction conventions. READ THIS BEFORE WRITING AN ADAPTER.
#
# No validator can catch a convention error -- both conventions are angles in
# [0, 360) and both look correct. Getting it wrong silently reverses vectors,
# which means routing.py cheerfully steers a boat INTO the hazard it was
# avoiding. So the convention is pinned per variable and stated at the point
# where an adapter would get it wrong.
#
#   "from" = meteorological. 90 deg means the wind/waves COME FROM the east.
#   "to"   = oceanographic.  90 deg means the current FLOWS TOWARD the east.
#
# These are the standard conventions in their respective fields, which is
# exactly why they are easy to mix up: wind and current use OPPOSITE ones.
DIRECTION_CONVENTION: dict[Variable, str] = {
    Variable.WAVE_DIRECTION: "from",
    Variable.WIND_DIRECTION: "from",
    Variable.CURRENT_DIRECTION: "to",
}

# ---------------------------------------------------------------------------
# Plausible physical ranges, inclusive. These are not quality control -- they
# are unit-error detection. A 500 m wave height is not a stormy sea, it is an
# adapter that read a different column. Reject, never clamp: a clamped value is
# indistinguishable from a real one downstream.
PHYSICAL_RANGES: dict[Variable, tuple[float, float]] = {
    Variable.WAVE_HEIGHT: (0.0, 30.0),      # world record individual wave ~26 m
    Variable.WAVE_PERIOD: (0.0, 30.0),
    Variable.WAVE_DIRECTION: (0.0, 360.0),
    Variable.WIND_SPEED: (0.0, 120.0),      # ~430 km/h, above any recorded gust
    Variable.WIND_DIRECTION: (0.0, 360.0),
    Variable.SST: (-5.0, 45.0),             # -5 catches a kelvin value instantly
    Variable.CHL: (0.0, 100.0),
    Variable.CURRENT_SPEED: (0.0, 15.0),
    Variable.CURRENT_DIRECTION: (0.0, 360.0),
}


class Observation(BaseModel):
    """One value, at one place, at one time, from one source.

    The ONE shape every adapter outputs and the only shape that enters the
    observations table.

    frozen: an Observation is a record of what a source said. Nothing may edit
    it after construction -- corrections are new rows, so the trace stays
    truthful about what was known when.

    extra="forbid": a typo'd field name is a hard error rather than a silently
    dropped value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)

    valid_time: datetime      # when the observation/forecast APPLIES
    # issued_time is only interpretable together with issued_time_kind.
    # Never do data-age arithmetic with one and not the other.
    issued_time: datetime | None = None
    issued_time_kind: IssuedTimeKind | None = None
    lat: float
    lon: float
    variable: Variable
    value: float
    unit: str
    source_id: str
    confidence: float | None = None

    # -- derived ------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def h3_cell(self) -> str:
        """The H3 cell containing (lat, lon), at the one global resolution.

        Computed, never supplied. An adapter physically cannot pass a cell that
        disagrees with its own coordinates -- same reasoning as the generated
        geom column in the schema. (extra="forbid" rejects an attempt to.)
        """
        return h3.latlng_to_cell(self.lat, self.lon, H3_RESOLUTION)

    # -- validators ---------------------------------------------------------
    @field_validator("valid_time", "issued_time")
    @classmethod
    def _require_timezone(cls, v: datetime | None) -> datetime | None:
        """Reject naive datetimes.

        The columns are timestamptz. A naive datetime gets interpreted in the
        server's timezone, so an adapter that drops the tzinfo shifts every
        one of its timestamps by the UTC offset -- 5h30m here. That turns a
        current forecast into a stale one and a stale one into a future one,
        with no error anywhere.
        """
        if v is not None and v.tzinfo is None:
            raise ValueError(
                "datetime must be timezone-aware; got a naive value. "
                "Parse the source's time convention explicitly and attach UTC."
            )
        return v

    @field_validator("lat")
    @classmethod
    def _check_lat(cls, v: float) -> float:
        if not -90.0 <= v <= 90.0:
            raise ValueError(f"lat {v} outside [-90, 90]")
        return v

    @field_validator("lon")
    @classmethod
    def _check_lon(cls, v: float) -> float:
        if not -180.0 <= v <= 180.0:
            raise ValueError(f"lon {v} outside [-180, 180]")
        return v

    @field_validator("value")
    @classmethod
    def _check_finite(cls, v: float) -> float:
        # Sources encode "no data" as NaN in NetCDF and as null in JSON. NaN
        # must never reach the table: it propagates through fusion arithmetic
        # and poisons an entire risk cell. Adapters drop missing values; they
        # do not emit them.
        if math.isnan(v) or math.isinf(v):
            raise ValueError(
                "value must be finite; drop missing readings in the adapter "
                "rather than emitting NaN/inf"
            )
        return v

    @field_validator("confidence")
    @classmethod
    def _check_confidence(cls, v: float | None) -> float | None:
        if v is not None and not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence {v} outside [0, 1]")
        return v

    @model_validator(mode="after")
    def _check_issued_pairing(self) -> Observation:
        """issued_time and its kind travel together or not at all.

        A timestamp without its kind cannot be told apart from a real issue
        time downstream, which is the precise confusion this field exists to
        prevent. Mirrors the observations_issued_paired CHECK constraint.
        """
        if (self.issued_time is None) != (self.issued_time_kind is None):
            raise ValueError(
                "issued_time and issued_time_kind must both be set or both be "
                "None; got issued_time="
                f"{self.issued_time!r}, issued_time_kind={self.issued_time_kind!r}"
            )
        return self

    @model_validator(mode="after")
    def _check_unit_and_range(self) -> Observation:
        """The two checks this whole contract exists for."""
        expected = CANONICAL_UNITS[self.variable]
        if self.unit != expected:
            raise ValueError(
                f"unit mismatch for {self.variable.value}: got {self.unit!r}, "
                f"canonical is {expected!r}. Convert in the adapter -- core/ "
                f"never converts units."
            )

        lo, hi = PHYSICAL_RANGES[self.variable]
        if not lo <= self.value <= hi:
            raise ValueError(
                f"{self.variable.value} = {self.value} {self.unit} is outside "
                f"the plausible range [{lo}, {hi}]. This is almost always a "
                f"unit or column error in the adapter, not real weather."
            )
        return self

    # -- helpers ------------------------------------------------------------
    def age_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds since issued_time, or None if unknown.

        The data-age every trace has to report alongside the number. Read this
        WITH issued_time_kind: when the kind is FETCH_PROXY the result is a
        lower bound on true age (the source may have been sitting on the value
        long before we fetched it), not the age itself.
        """
        if self.issued_time is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.issued_time).total_seconds()


# ---------------------------------------------------------------------------
# Insert. geom is the RAW sample location, not the cell centroid: h3_cell
# already carries the cell, and observations has no lat/lon columns, so the
# centroid would be the point at which the true position is lost forever.
#
# ON CONFLICT makes re-running a fetch idempotent -- re-ingesting an overlapping
# forecast window updates in place instead of duplicating.
_INSERT_SQL = """
INSERT INTO observations
    (valid_time, issued_time, issued_time_kind, h3_cell, variable, value, unit,
     source_id, confidence, geom)
VALUES
    (%s, %s, %s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
ON CONFLICT (source_id, h3_cell, variable, valid_time) DO UPDATE SET
    issued_time      = EXCLUDED.issued_time,
    issued_time_kind = EXCLUDED.issued_time_kind,
    value       = EXCLUDED.value,
    unit        = EXCLUDED.unit,
    confidence  = EXCLUDED.confidence,
    geom        = EXCLUDED.geom
"""


def _as_row(obs: Observation) -> tuple[Any, ...]:
    return (
        obs.valid_time,
        obs.issued_time,
        obs.issued_time_kind.value if obs.issued_time_kind else None,
        obs.h3_cell,
        obs.variable.value,
        obs.value,
        obs.unit,
        obs.source_id,
        obs.confidence,
        obs.lon,  # ST_MakePoint is (x, y) = (lon, lat). Swapping these puts
        obs.lat,  # the Bay of Bengal in Somalia and still "works".
    )


def _batched(seq: Sequence[Observation], n: int) -> Iterator[Sequence[Observation]]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


_RELIABILITY_SQL = "SELECT reliability FROM sources WHERE source_id = %s"


async def fetch_reliability(source_id: str) -> float:
    """Read a source's reliability prior from the sources table.

    Lives here rather than inside Adapter so that normalize() stays a pure
    function: the ingest job calls this once and passes the value into the
    adapter's constructor, instead of the adapter reaching for the database
    mid-translation.
    """
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_RELIABILITY_SQL, (source_id,))
            row = await cur.fetchone()
    if row is None:
        raise LookupError(
            f"source_id {source_id!r} is not in the sources table. Seed it in "
            f"db/03_seed_sources.sql -- the FK on observations will reject it."
        )
    return float(row[0])


class Adapter(ABC):
    """Base class for every source translator.

    Subclasses implement fetch() and normalize(). run() is inherited and
    should not be overridden -- it is what guarantees that everything reaching
    the table passed the same contract.
    """

    #: Must match a sources.source_id row seeded in db/03_seed_sources.sql.
    source_id: ClassVar[str]

    def __init_subclass__(cls, *, abstract: bool = False, **kwargs: Any) -> None:
        """Catch a missing source_id when the class is DEFINED, not when an
        ingest job dies against a foreign key.

        Intermediate bases that share request logic between several real
        adapters have no source_id of their own and opt out explicitly:

            class _OpenMeteoAdapter(Adapter, abstract=True): ...

        The opt-out is a keyword rather than a name convention or an
        __abstractmethods__ check, because such a base implements fetch() and
        normalize() -- so it looks concrete to abc and would slip through.
        """
        super().__init_subclass__(**kwargs)
        cls._is_abstract = abstract
        if not abstract and not getattr(cls, "source_id", None):
            raise TypeError(
                f"{cls.__name__} must set a class-level source_id matching a "
                f"row in the sources table, or pass abstract=True if it is a "
                f"shared base rather than a real adapter."
            )

    @abstractmethod
    async def fetch(self) -> Any:
        """Get raw payload from upstream. No parsing beyond what the transport
        requires (e.g. response.json()). Returns whatever shape the source
        gives -- normalize() is what understands it.
        """

    @abstractmethod
    def normalize(self, raw: Any) -> list[Observation]:
        """Translate the raw payload into Observations.

        This is a pure function: same input, same output, no I/O, no clock.
        Unit conversion and direction-convention alignment happen HERE and
        nowhere else. Missing readings are dropped, not emitted as NaN.
        """

    async def run(self) -> int:
        """fetch -> normalize -> write. Returns rows written.

        Do not override: this is what guarantees everything reaching the table
        went through the same contract.
        """
        raw = await self.fetch()
        return await self.write(self.normalize(raw))

    async def write(self, observations: Sequence[Observation]) -> int:
        """Upsert already-normalized observations. Returns rows written.

        Split out of run() so a caller that wants to inspect or report on the
        observations before they land (an ingest job tallying per variable,
        say) can do so without fetching twice.
        """
        if not observations:
            log.warning("%s: nothing to write", self.source_id)
            return 0

        # An adapter must not write under another source's id: the whole
        # provenance chain, and fusion.py's reliability prior, key off it.
        for obs in observations:
            if obs.source_id != self.source_id:
                raise ValueError(
                    f"{type(self).__name__} emitted source_id={obs.source_id!r} "
                    f"but is registered as {self.source_id!r}"
                )

        written = 0
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                for batch in _batched(observations, INSERT_BATCH_SIZE):
                    await cur.executemany(_INSERT_SQL, [_as_row(o) for o in batch])
                    written += len(batch)

        log.info("%s: wrote %d observations", self.source_id, written)
        return written
