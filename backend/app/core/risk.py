"""Probabilistic marine hazard field.

WHAT THIS IS NOT
----------------
It is not a weighted sum of normalized hazard variables. That pattern --
0.4*wave + 0.4*wind + 0.2*current, call it a "risk score" -- is what most
dashboards do, and it produces a dimensionless number that cannot be checked
against anything. Nobody can say whether a score of 0.62 was right, because
0.62 of what? It has no units, no failure definition and no calibration.

WHAT IT IS
----------
For each cell and each hour we answer one question with a definite meaning:

    What is the probability that a hazard variable EXCEEDS a threshold at
    which small vessels are actually in danger?

That number is falsifiable. If we say P(wave > 2.5 m) = 0.30 for 1000 cell-
hours, waves should exceed 2.5 m in about 300 of them, and if they do not the
model is wrong in a way we can measure. That is the whole point.

It also behaves correctly where a threshold check does not. A deterministic
forecast of 1.8 m against a 2.5 m threshold reports "safe, 0%". But the
forecast has error, and the true state might be 2.6 m. We report ~4% -- small,
honest, and non-zero. Over hundreds of boats, 4% is not nothing.

HOW UNCERTAINTY IS ESTIMATED -- READ THIS BEFORE TRUSTING A NUMBER
-----------------------------------------------------------------
Exceedance probability needs a distribution, not a point value. The right
source of one is a forecast ENSEMBLE: run the model many times from perturbed
initial conditions and use the spread of outcomes.

WE DO NOT HAVE AN ENSEMBLE. Open-Meteo returns a single deterministic value
per variable per hour. So sigma here is a STAND-IN, built from two things:

  (a) disagreement between sources that cover the same cell, variable and
      time -- a real, data-derived signal, but only available where coverage
      overlaps, which today is nowhere (marine and weather carry disjoint
      variables);
  (b) a documented per-variable base uncertainty constant that grows with
      lead time, from published NWP verification statistics.

So in practice, right now, sigma is (b) alone. Every record carries
sigma_basis saying which it was. Nothing here is called an ensemble, because
it is not one. When Copernicus lands with real ensemble members, (b) is
replaced by measured spread and this docstring should shrink considerably.

Treat a probability from a base-only sigma as an order-of-magnitude statement,
not a calibrated one.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Sequence

from scipy.stats import norm

from app.adapters.base import IssuedTimeKind, Variable
from app.config import SCENARIO_SOURCE_IDS
from app.core.grid import ObsRow, neighbours

log = logging.getLogger(__name__)


# ===========================================================================
# Hazard definitions
# ===========================================================================
@dataclass(frozen=True, slots=True)
class HazardSpec:
    """One hazard variable, its danger threshold, and how wrong we expect the
    forecast to be.
    """

    variable: Variable
    threshold: float
    unit: str

    #: Forecast standard error at zero lead time.
    sigma_base0: float
    #: Growth in that error per hour of lead time. Forecast skill decays with
    #: horizon; a flat sigma would make hour 71 look as trustworthy as hour 1,
    #: and routing.py plans over exactly that horizon.
    sigma_growth_per_h: float

    rationale: str


# Thresholds are small-craft-advisory scale: the sea state at which the boats
# this system exists to recall are in real trouble, not the sea state at which
# a ship is.
#
# The sigma constants are order-of-magnitude values from published operational
# NWP/wave verification. They are deliberately CONSTANTS IN ONE PLACE with
# their reasoning attached, rather than magic numbers inline, because they are
# the least defensible part of this module and the first thing a real ensemble
# replaces.
HAZARDS: tuple[HazardSpec, ...] = (
    HazardSpec(
        variable=Variable.WAVE_HEIGHT,
        threshold=2.5,
        unit="m",
        sigma_base0=0.30,
        sigma_growth_per_h=0.010,   # ~1.0 m total sigma at 72 h
        rationale=(
            "2.5 m significant wave height is the scale at which Indian coastal "
            "advisories warn small fishing craft. Operational global wave models "
            "run ~0.25-0.35 m RMSE at analysis time, growing through the "
            "forecast."
        ),
    ),
    HazardSpec(
        variable=Variable.WIND_SPEED,
        threshold=12.5,
        unit="m/s",
        sigma_base0=1.20,
        sigma_growth_per_h=0.035,   # ~3.7 m/s total sigma at 72 h
        rationale=(
            "12.5 m/s (~45 km/h, ~24 kt) is small-craft-advisory scale. 10 m "
            "wind speed RMSE in operational NWP is ~1-1.5 m/s at analysis, "
            "growing to several m/s by day 3."
        ),
    ),
)


# ===========================================================================
# Uncertainty scoring
# ===========================================================================
# hazard_prob answers "how likely is the hazard". uncertainty answers "how much
# should you trust that number". They are different questions and are stored in
# different columns on purpose: a hazard_prob of 0.05 computed from two
# agreeing sources five minutes old is not the same claim as 0.05 inferred from
# one borrowed neighbour value with a constant for a sigma.
#
# This is an ADDITIVE HEURISTIC, not a calibrated probability, and it must not
# be read as one. What it does guarantee is monotonicity: every term below only
# ever makes the score worse, each is recorded individually in drivers, and the
# total is clamped to [0, 1]. So it can be audited term by term rather than
# taken on faith.
U_BASE_ONLY = 0.30          # sigma from constants; no cross-source check
U_MISSING_VARIABLE = 0.35   # a hazard variable has no data at all
U_NEIGHBOUR_FILL = 0.15     # value borrowed from an adjacent cell
U_FETCH_PROXY = 0.10        # data-age is a lower bound only (see base.py)
U_AGE_PER_DAY = 0.10        # staleness, prorated


# ===========================================================================
# Per-variable estimate
# ===========================================================================
@dataclass(slots=True)
class VariableEstimate:
    """What we believe about one variable in one cell at one instant."""

    variable: Variable
    mu: float                  # fused central value
    sigma: float               # total uncertainty (base + disagreement)
    sigma_base: float
    sigma_disagreement: float
    sigma_basis: str           # "base_only" | "base_plus_disagreement"
    p_exceed: float
    n_sources: int
    sources: list[str]
    origin: str                # "self" | "neighbour"
    lead_hours: float
    weighting: str             # "reliability" | "equal_fallback_zero_reliability"
    oldest_issued: datetime | None
    has_fetch_proxy: bool
    simulated: bool            # any contributing source is a scenario source

    def as_dict(self, spec: HazardSpec) -> dict[str, Any]:
        return {
            "mu": round(self.mu, 4),
            "unit": spec.unit,
            "threshold": spec.threshold,
            "p_exceed": round(self.p_exceed, 6),
            "sigma": round(self.sigma, 4),
            "sigma_base": round(self.sigma_base, 4),
            "sigma_disagreement": round(self.sigma_disagreement, 4),
            "sigma_basis": self.sigma_basis,
            "n_sources": self.n_sources,
            "sources": self.sources,
            "origin": self.origin,
            "lead_hours": round(self.lead_hours, 2),
            "weighting": self.weighting,
            "has_fetch_proxy": self.has_fetch_proxy,
            "simulated": self.simulated,
        }


def fuse(rows: Sequence[ObsRow]) -> tuple[float, float, str]:
    """Combine several sources' values for ONE variable into (mu, spread, how).

    Reliability-weighted mean, using the per-source priors from the sources
    table. This is PROVISIONAL: proper co-registration, conflict resolution and
    uncertainty propagation across sources is core/fusion.py's job, and this
    function should move there and grow when it exists. It lives here now so
    risk.py can run before fusion.py is written.

    The spread returned is the weighted population standard deviation across
    sources -- genuine, data-derived disagreement. It is 0.0 when only one
    source covers the variable, which is not a claim of certainty: it means we
    had nothing to disagree with, and the caller must fall back to sigma_base.
    """
    if not rows:
        raise ValueError("fuse() called with no rows")

    weights = [r.reliability for r in rows]
    total_w = sum(weights)

    # A source with reliability 0.0 contributes zero weight. That is intended
    # for scenario/synthetic sources, which must not sway a fused estimate.
    # But if EVERY contributing source has zero reliability, the weighted mean
    # is 0/0 -- so fall back to equal weights and say so, rather than dividing
    # by zero or silently dropping the data.
    if total_w <= 0.0:
        weights = [1.0] * len(rows)
        total_w = float(len(rows))
        weighting = "equal_fallback_zero_reliability"
    else:
        weighting = "reliability"

    mu = sum(w * r.value for w, r in zip(weights, rows)) / total_w

    if len(rows) < 2:
        return mu, 0.0, weighting

    variance = sum(w * (r.value - mu) ** 2 for w, r in zip(weights, rows)) / total_w
    return mu, math.sqrt(variance), weighting


def sigma_for(spec: HazardSpec, lead_hours: float, disagreement: float) -> tuple[float, float, str]:
    """Total sigma = base(lead) and disagreement added in quadrature.

    In quadrature because they are independent error sources: base sigma is the
    model's own expected error, disagreement is how much the sources differ.
    Adding them linearly would double-count; taking the max would discard one.
    """
    sigma_base = spec.sigma_base0 + spec.sigma_growth_per_h * max(0.0, lead_hours)
    if disagreement > 0.0:
        return math.hypot(sigma_base, disagreement), sigma_base, "base_plus_disagreement"
    return sigma_base, sigma_base, "base_only"


def exceedance_probability(mu: float, sigma: float, threshold: float) -> float:
    """P(X > threshold) for X ~ Normal(mu, sigma) truncated to [0, inf).

    Truncated because wave height and wind speed cannot be negative. In calm
    conditions sigma is a large fraction of mu -- mu=0.5 m with sigma=0.5 m
    puts 16% of an untruncated normal's mass below zero, on physically
    impossible states. Conditioning that away is:

        P(X > t | X > 0) = P(X > t) / P(X > 0)

    Written with the closed form rather than scipy.stats.truncnorm because the
    two-line version is inspectable, and this is the single most important
    number the system produces.
    """
    if sigma <= 0.0:
        # Degenerate: no uncertainty at all. Falls back to a hard threshold
        # check, which is the honest reading of "sigma is exactly zero".
        return 1.0 if mu > threshold else 0.0

    p_above_threshold = norm.sf(threshold, loc=mu, scale=sigma)
    p_above_zero = norm.sf(0.0, loc=mu, scale=sigma)

    if p_above_zero <= 0.0:
        return 0.0
    return float(min(1.0, max(0.0, p_above_threshold / p_above_zero)))


# ===========================================================================
# Cell-level result
# ===========================================================================
@dataclass(slots=True)
class RiskCell:
    valid_time: datetime
    h3_cell: str
    hazard_prob: float | None   # None = no data at all. NOT the same as 0.0.
    uncertainty: float
    #: HARD GUARD -- True if any input came from a scenario source. Carried as
    #: a field, stored as a column, and surfaced by the API, so a simulated
    #: number can never be presented as a real forecast.
    simulated: bool = False
    drivers: dict[str, Any] = field(default_factory=dict)


def combine(probabilities: dict[Variable, float]) -> tuple[float, float]:
    """Combine per-variable exceedance probabilities into one cell hazard.

    Returns (independent_estimate, perfectly_correlated_estimate).

    INDEPENDENCE ASSUMPTION, STATED PLAINLY:

        P(any hazard) = 1 - PROD(1 - p_i)

    assumes wave and wind exceedances are independent events. THEY ARE NOT.
    Wind generates waves, so the two are strongly positively correlated -- the
    hours when wind exceeds 12.5 m/s are largely the same hours when waves
    exceed 2.5 m, not additional ones.

    Independence therefore OVER-COUNTS, and the combined number is biased HIGH.
    We keep it anyway, deliberately: this drives vessel recall, and for a
    recall decision the safe direction to be wrong in is "too cautious". But
    biased-high is a property to state, not to hide.

    The perfectly-correlated case, max(p_i), is the other end of the range. We
    return and store BOTH, so every row carries an explicit interval rather
    than one number whose sensitivity to the assumption is invisible. The true
    value lies between them.
    """
    if not probabilities:
        return 0.0, 0.0
    survival = 1.0
    for p in probabilities.values():
        survival *= 1.0 - p
    return 1.0 - survival, max(probabilities.values())


def _estimate(
    index: dict[tuple[str, datetime, Variable], list[ObsRow]],
    cell: str,
    when: datetime,
    spec: HazardSpec,
    neighbour_k: int,
) -> VariableEstimate | None:
    """Best available estimate of one variable in one cell, or None.

    Prefers observations in the cell itself. Falls back to neighbouring cells
    only if the cell has none.

    WHY THE FALLBACK EXISTS: the marine and weather endpoints snap to different
    model grids, so one point on the map becomes two adjacent H3 cells -- 12 of
    20 AOI cells coincide and the other 8 sit at exactly grid distance 1.
    Without borrowing, almost every cell would have wind or waves but never
    both, and the risk field would be near-empty.

    Borrowing is a CRUDE STAND-IN for the spatial co-registration core/fusion.py
    is meant to do properly (interpolate onto a common grid, propagate the
    interpolation error). It is not interpolation: it takes neighbouring values
    at face value and pays for it with an uncertainty penalty. Every borrowed
    value is tagged origin="neighbour" so no consumer can mistake it for a
    measurement in the cell.
    """
    rows = index.get((cell, when, spec.variable), [])
    origin = "self"

    if not rows:
        borrowed: list[ObsRow] = []
        for other in neighbours(cell, neighbour_k):
            borrowed.extend(index.get((other, when, spec.variable), []))
        if not borrowed:
            return None
        rows, origin = borrowed, "neighbour"

    mu, disagreement, weighting = fuse(rows)

    # Lead time drives sigma growth. Use the OLDEST issue time among the
    # contributing rows: the longest lead, hence the largest sigma. Where
    # sources disagree about their own freshness, assume the less flattering
    # one.
    issued = [r.issued_time for r in rows if r.issued_time is not None]
    oldest = min(issued) if issued else None
    lead_hours = (when - oldest).total_seconds() / 3600.0 if oldest else 0.0

    sigma, sigma_base, basis = sigma_for(spec, lead_hours, disagreement)

    return VariableEstimate(
        variable=spec.variable,
        mu=mu,
        sigma=sigma,
        sigma_base=sigma_base,
        sigma_disagreement=disagreement,
        sigma_basis=basis,
        p_exceed=exceedance_probability(mu, sigma, spec.threshold),
        n_sources=len({r.source_id for r in rows}),
        sources=sorted({r.source_id for r in rows}),
        origin=origin,
        lead_hours=lead_hours,
        weighting=weighting,
        oldest_issued=oldest,
        has_fetch_proxy=any(
            r.issued_time_kind == IssuedTimeKind.FETCH_PROXY.value for r in rows
        ),
        # Propagates through neighbour fill too: a value BORROWED from a
        # scenario cell makes this cell simulated just as surely as one
        # measured in it.
        simulated=any(r.source_id in SCENARIO_SOURCE_IDS for r in rows),
    )


def compute_cell(
    index: dict[tuple[str, datetime, Variable], list[ObsRow]],
    cell: str,
    when: datetime,
    now: datetime,
    hazards: Sequence[HazardSpec] = HAZARDS,
    neighbour_k: int = 1,
) -> RiskCell:
    """The risk for one cell at one instant.

    `now` is passed in rather than read from the clock so this stays a pure
    function -- same inputs, same output, testable and reproducible.
    """
    estimates: dict[Variable, VariableEstimate] = {}
    missing: list[str] = []

    for spec in hazards:
        est = _estimate(index, cell, when, spec, neighbour_k)
        if est is None:
            missing.append(spec.variable.value)
        else:
            estimates[spec.variable] = est

    spec_by_var = {s.variable: s for s in hazards}

    # ---- no data at all -------------------------------------------------
    # hazard_prob is NULL, NOT 0.0. "We have no information about this cell"
    # and "this cell is safe" are opposite claims, and collapsing the first
    # into the second is how a recall system gets someone killed. A land cell
    # and a becalmed cell must not read the same.
    if not estimates:
        return RiskCell(
            valid_time=when,
            h3_cell=cell,
            hazard_prob=None,
            uncertainty=1.0,
            simulated=False,
            drivers={
                "no_coverage": True,
                "missing": missing,
                "note": "no observations for any hazard variable; "
                        "hazard_prob is unknown, not zero",
            },
        )

    probs = {v: e.p_exceed for v, e in estimates.items()}
    hazard_prob, hazard_prob_max = combine(probs)

    # ---- leave-one-out attribution --------------------------------------
    # How much of the final number does each variable actually account for?
    # Recomputing without variable j and differencing is a real attribution;
    # normalizing p_i into "shares" would be a decorative one.
    contributions: dict[str, float] = {}
    for v in probs:
        without = {k: p for k, p in probs.items() if k != v}
        contributions[v.value] = round(hazard_prob - combine(without)[0], 6)

    # ---- uncertainty ----------------------------------------------------
    terms: dict[str, float] = {}

    n_base_only = sum(1 for e in estimates.values() if e.sigma_basis == "base_only")
    if n_base_only:
        terms["sigma_base_only"] = round(
            U_BASE_ONLY * n_base_only / len(estimates), 4
        )

    if missing:
        # Partial coverage. hazard_prob below is computed from the variables we
        # DO have, which makes it a LOWER BOUND -- the missing variable can
        # only add hazard, never remove it.
        terms["missing_variables"] = round(U_MISSING_VARIABLE * len(missing), 4)

    n_borrowed = sum(1 for e in estimates.values() if e.origin == "neighbour")
    if n_borrowed:
        terms["neighbour_fill"] = round(
            U_NEIGHBOUR_FILL * n_borrowed / len(estimates), 4
        )

    if any(e.has_fetch_proxy for e in estimates.values()):
        # issued_time is a fetch timestamp, so the age computed from it is a
        # LOWER bound on true age. Penalise what we cannot see.
        terms["fetch_proxy_age"] = U_FETCH_PROXY

    issued_times = [e.oldest_issued for e in estimates.values() if e.oldest_issued]
    if issued_times:
        age_days = max(0.0, (now - min(issued_times)).total_seconds() / 86400.0)
        if age_days > 0:
            terms["data_age"] = round(min(U_AGE_PER_DAY * age_days, 0.5), 4)

    uncertainty = min(1.0, sum(terms.values()))

    # The guard. ANY simulated input taints the whole cell -- there is no
    # partially-simulated forecast, because a consumer cannot act on half a
    # number. Deliberately not weighted, discounted or thresholded: it is a
    # provenance fact, not a quality score.
    simulated = any(e.simulated for e in estimates.values())
    simulated_sources = sorted(
        {s for e in estimates.values() for s in e.sources if s in SCENARIO_SOURCE_IDS}
    )

    return RiskCell(
        valid_time=when,
        h3_cell=cell,
        hazard_prob=hazard_prob,
        uncertainty=uncertainty,
        simulated=simulated,
        drivers={
            "simulated": simulated,
            "simulated_sources": simulated_sources,
            "hazard_prob": round(hazard_prob, 6),
            "combine_rule": "independent_noisy_or",
            "combine_note": (
                "wind and waves are positively correlated, so the independent "
                "combination is biased HIGH; hazard_prob_if_fully_correlated is "
                "the other bound and the truth lies between them"
            ),
            "hazard_prob_if_fully_correlated": round(hazard_prob_max, 6),
            "partial_coverage": bool(missing),
            "coverage": sorted(v.value for v in estimates),
            "missing": missing,
            "lower_bound": bool(missing),
            "contributions": contributions,
            "variables": {
                v.value: e.as_dict(spec_by_var[v]) for v, e in estimates.items()
            },
            "uncertainty_terms": terms,
        },
    )


def compute_risk_field(
    index: dict[tuple[str, datetime, Variable], list[ObsRow]],
    cells: Iterable[str],
    times: Iterable[datetime],
    now: datetime,
    hazards: Sequence[HazardSpec] = HAZARDS,
    neighbour_k: int = 1,
) -> list[RiskCell]:
    """The full field: every cell at every time step."""
    times = list(times)
    return [
        compute_cell(index, cell, when, now, hazards, neighbour_k)
        for cell in cells
        for when in times
    ]


# ===========================================================================
# Persistence
# ===========================================================================
_UPSERT_SQL = """
INSERT INTO risk_cells
    (valid_time, h3_cell, hazard_prob, uncertainty, simulated, drivers)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (valid_time, h3_cell) DO UPDATE SET
    hazard_prob = EXCLUDED.hazard_prob,
    uncertainty = EXCLUDED.uncertainty,
    simulated   = EXCLUDED.simulated,
    drivers     = EXCLUDED.drivers
"""


async def write_risk_cells(conn: Any, cells: Sequence[RiskCell], batch_size: int = 1000) -> int:
    """Upsert computed risk cells. Takes a connection; see grid.fetch_observations."""
    from psycopg.types.json import Json

    written = 0
    async with conn.cursor() as cur:
        for i in range(0, len(cells), batch_size):
            batch = cells[i : i + batch_size]
            await cur.executemany(
                _UPSERT_SQL,
                [
                    (c.valid_time, c.h3_cell, c.hazard_prob, c.uncertainty,
                     c.simulated, Json(c.drivers))
                    for c in batch
                ],
            )
            written += len(batch)
    return written
