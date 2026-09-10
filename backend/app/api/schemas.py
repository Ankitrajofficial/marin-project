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


# ---------------------------------------------------------------------------
# /api/geofence and /api/zones
# ---------------------------------------------------------------------------
class ZoneHitOut(BaseModel):
    zone_id: str
    zone_type: str
    name: str | None
    #: 'official' | 'open_data_advisory'. Required, no default -- same guard as
    #: `simulated`: an endpoint cannot forget to say whether a boundary carries
    #: legal authority.
    authority: str
    attribution: str

    inside: bool
    verdict: str                      # 'inside' | 'alert' | 'clear'
    #: Geodesic distance to the zone EDGE, nautical miles.
    distance_nm: float
    #: distance_nm minus the uncertainty budget. The verdict is computed from
    #: THIS, never from distance_nm.
    effective_distance_nm: float
    margin_nm: float
    bearing_deg: float | None
    closest_lat: float | None
    closest_lon: float | None
    meta: dict[str, Any] = Field(default_factory=dict)


class GeofenceResponse(BaseModel):
    lat: float
    lon: float
    buffer_nm: float
    verdict: str

    inside: list[ZoneHitOut]
    alerts: list[ZoneHitOut]
    nearest_by_type: dict[str, ZoneHitOut]

    #: REQUIRED, no default. True while every loaded zone is open data.
    #: MarineRegions is a VLIZ compilation and OSM is crowd-sourced; neither is
    #: Survey of India and neither carries legal authority. A client rendering
    #: a distance-to-IMBL without this is presenting an advisory line as a
    #: legal one.
    advisory_only: bool
    #: Attribution strings that MUST be displayed alongside any of these
    #: numbers. Required, not defaulted, for the same reason.
    attributions: list[str]
    disclaimer: str


class ZoneProperties(BaseModel):
    zone_id: str
    zone_type: str
    name: str | None
    authority: str
    attribution: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ZoneFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: ZoneProperties


class ZoneFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    n_features: int
    advisory_only: bool
    attributions: list[str]
    disclaimer: str
    features: list[ZoneFeature]


# ---------------------------------------------------------------------------
# /api/recall
# ---------------------------------------------------------------------------
class HarbourOut(BaseModel):
    harbour_id: str
    name: str
    lat: float
    lon: float
    harbour_type: str | None
    distance_nm: float
    #: 'osm' = a real tagged depth. 'unknown' = OSM had none, which is every
    #: harbour in the AOI today. Required, so a client cannot render a target
    #: harbour without knowing whether its depth was ever checked.
    depth_source: str
    depth_m: float | None
    #: None means UNVERIFIABLE, not True. A client must not render None as a
    #: tick.
    draft_ok: bool | None


class RecallEntryOut(BaseModel):
    mmsi: str
    name: str | None
    vessel_class: str | None
    lat: float
    lon: float
    h3_cell: str
    draft_m: float | None

    position_time: datetime
    position_age_minutes: float
    position_is_stale: bool
    #: age x speed -- how far the vessel could be from the plotted point.
    position_uncertainty_nm: float

    speed_ms: float
    speed_source: str
    speed_samples: int

    harbour: HarbourOut | None
    time_to_harbour_h: float | None
    time_to_hazard_h: float | None
    hazard_time: datetime | None
    hazard_prob_at_crossing: float | None
    hazard_driver: str | None
    hazard_data_age_minutes: float | None

    #: time_to_hazard - time_to_harbour. Negative = cannot make it.
    margin_h: float | None
    status: str
    reasons: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    simulated: bool


class RecallResponse(BaseModel):
    generated_at: datetime
    threshold: float
    #: Straight-line distance x this factor stands in for a real route until
    #: core/routing.py exists. Reported because it changes every margin.
    detour_factor: float
    distance_is_straight_line: bool

    n_vessels: int
    n_harbours: int
    #: Ranked ascending by margin -- smallest slack first.
    ranked: list[RecallEntryOut]
    #: NEVER merged into `ranked`. A vessel that could not be evaluated must
    #: reach a human, not sink to the bottom of a list.
    cannot_assess: list[RecallEntryOut]

    simulated: bool
    #: Plain-language limits of this particular result. Present so a thin
    #: ranking is read as thin data, not as a calm sea.
    caveats: list[str]
