"""API response models.

These are not documentation -- they are enforcement. `simulated` is a REQUIRED
field with no default on every model that can carry a hazard number, so an
endpoint that forgets it raises a serialization error instead of quietly
serving a simulated forecast as a real one. That is the CLAUDE.md guard,
expressed where it cannot be skipped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# /api/risk
# ---------------------------------------------------------------------------
class RiskProperties(BaseModel):
    h3_cell: str
    # None means NO DATA, not zero risk. Clients must render it as unknown --
    # a land cell and a becalmed cell are opposite claims.
    hazard_prob: float | None
    uncertainty: float
    #: Required, no default. See module docstring.
    simulated: bool
    simulated_sources: list[str]
    drivers: dict[str, Any]


class RiskFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: RiskProperties


class RiskFeatureCollection(BaseModel):
    """GeoJSON FeatureCollection plus the metadata a map actually needs."""

    type: Literal["FeatureCollection"] = "FeatureCollection"

    #: The time actually served. May differ from what was requested: times are
    #: snapped to the nearest available hour, and a client that displayed its
    #: own requested time instead of this one would mislabel the map.
    valid_time: datetime
    requested_time: datetime | None = None

    #: True if ANY feature is simulated. Redundant with the per-feature flag on
    #: purpose: a client that reads only collection-level metadata still cannot
    #: present a simulated field as a real forecast.
    simulated: bool

    n_features: int
    #: Highest hazard_prob in this response. The legend shows it so a fixed
    #: 0-1 colour scale reads as "calm sea" rather than "broken map".
    hazard_prob_max: float | None
    features: list[RiskFeature]


class RiskTimes(BaseModel):
    """Available time steps, for the frontend slider."""

    times: list[datetime]
    count: int
    first: datetime | None
    last: datetime | None


# ---------------------------------------------------------------------------
# /api/cells/{h3_cell}/trace
# ---------------------------------------------------------------------------
class TraceObservation(BaseModel):
    source_id: str
    variable: str
    value: float
    unit: str
    valid_time: datetime
    issued_time: datetime | None
    issued_time_kind: str | None

    age_seconds: float | None
    #: True when issued_time_kind is 'fetch_proxy': the source publishes no
    #: model run time, so issued_time is when WE fetched. The age below is
    #: therefore a LOWER bound on true staleness. Surfaced explicitly because a
    #: bare number would overstate freshness.
    age_is_lower_bound: bool

    #: 'self'      measured in this cell
    #: 'neighbour' borrowed from an adjacent cell because this one had no data
    origin: str
    from_cell: str
    reliability: float
    simulated: bool


class CellTrace(BaseModel):
    h3_cell: str
    valid_time: datetime
    requested_time: datetime | None = None

    hazard_prob: float | None
    uncertainty: float
    simulated: bool
    simulated_sources: list[str] = Field(default_factory=list)
    drivers: dict[str, Any]

    observations: list[TraceObservation]


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------
class SourceHealth(BaseModel):
    source_id: str
    name: str
    access_mode: str
    reliability: float
    n_observations: int
    latest_issued: datetime | None
    latest_valid: datetime | None
    issued_time_kinds: list[str]
    age_seconds: float | None
    age_is_lower_bound: bool


class Health(BaseModel):
    status: str
    database: str
    now: datetime
    sources: list[SourceHealth]
    n_risk_cells: int
    n_simulated_risk_cells: int
