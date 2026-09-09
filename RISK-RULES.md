# ORCA hazard rules

Engine: `orca-deterministic-rules-v1` · Source: [`src/agents/risk.ts`](src/agents/risk.ts)

## The one thing to know

**No language model participates in hazard classification.** `computeRisk()` is a pure
function of measured values and the thresholds below. The same inputs always produce the same
score, the same band, and the same list of triggered rules. A model plans the query and writes
the explanation; it never decides whether it is safe to go to sea.

This is deliberate and it is the answer to "what if the LLM hallucinates a hazard value?" —
it structurally cannot, because it is never asked.

## Vessel class

All thresholds are calibrated for a **small mechanised / motorised fishing vessel under 20 m
LOA** — the boat the majority of Indian marine fishers actually operate. A 40 m trawler would
need a different table; the engine takes the vessel class as an explicit field so a production
version can carry several.

## Scoring

Each triggered rule contributes `points`. The score is their sum, clamped to 0–100.

| Score | Band | Meaning |
|---|---|---|
| 0–33 | `SAFE` | Conditions within normal operating limits |
| 34–66 | `CAUTION` | Sail only with caution and a working radio |
| 67–100 | `UNSAFE` | Do not venture to sea |
| n/a | `UNKNOWN` | Sea state or wind data missing — **not** a clearance |

### Why `UNKNOWN` exists

If the ocean agent or the weather agent fails, a score of 0 means *"no hazard was found in the
data we have"*, which is **not** the same as *"it is safe"*. Reporting `SAFE` on absent data
would be the single most dangerous thing this system could do, so the engine refuses:

```
haveCore = ocean !== null && weather !== null
band = (!haveCore && score <= 66) ? UNKNOWN : ...normal bands
```

Missing data can never *downgrade* a hazard that was actually observed — a triggered
`UNSAFE`-weight rule still escalates normally.

## Threshold table

Wave, swell, wind, gust and rain values are the **worst hour** inside the requested time
window, not the mean, because a vessel meets the worst conditions of its trip, not the average.

### Sea state

| Rule | Condition | Points | Basis |
|---|---|---|---|
| `wave-danger` | significant wave height ≥ **2.5 m** | 34 | INCOIS issues High Wave Alerts for the Indian coast in this band; 2.5 m is the widely used small-craft danger line |
| `wave-caution` | significant wave height ≥ **1.5 m** | 18 | 1.5–2.5 m is the small-craft advisory band — workable but uncomfortable and unforgiving of engine failure |
| `swell-danger` | swell height ≥ **3.0 m** | 20 | Long-period swell surge; hazardous on approach and at harbour bars |
| `swell-caution` | swell height ≥ **2.0 m** | 10 | Swell of this height breaks dangerously at harbour mouths even when the open sea looks workable |
| `steep-sea` | wave period < **6 s** *and* wave height > 2 m | 10 | Short steep seas capsize small craft at wave heights a long swell would not. Period matters as much as height |

### Wind and weather

| Rule | Condition | Points | Basis |
|---|---|---|---|
| `wind-danger` | sustained wind ≥ **45 km/h** | 28 | IMD fishermen warnings advise not venturing out around this speed (≈ Beaufort 6–7) |
| `wind-caution` | sustained wind ≥ **25 km/h** | 12 | Beaufort 4–5: choppy sea, small-craft advisory territory |
| `gust-danger` | gusts ≥ **55 km/h** | 22 | Squall strength. Gusts, not sustained wind, are what knock a small boat down |
| `gust-caution` | gusts ≥ **40 km/h** | 8 | A gust factor well above sustained wind indicates active squall development |
| `precip-danger` | rainfall ≥ **35 mm** accumulated in window | 14 | IMD "heavy rain" band; visibility and bailing load both become serious |
| `precip-caution` | rainfall ≥ **10 mm** accumulated in window | 6 | IMD "moderate rain"; visibility degradation begins |
| `thunderstorm` | WMO weather code ≥ **95** | 20 | Thunderstorm / lightning. An open boat with a mast is the tallest object for miles |
| `visibility` | visibility < **2000 m** | 12 | Collision risk; aligns with the IMD dense-fog criterion |

### Restricted waters

| Rule | Condition | Points | Basis |
|---|---|---|---|
| `imbl-critical` | ≤ **2 nm** from a maritime boundary | 40 | Imminent crossing. Detention of Indian fishers across the India–Sri Lanka IMBL is a recurring, documented harm |
| `imbl-warning` | ≤ **5 nm** | 22 | Standard advisory buffer — enough sea room to alter course before it matters |
| `imbl-advisory` | ≤ **10 nm** | 8 | Awareness buffer; not yet a hazard |
| `mpa-inside` | inside a gazetted no-fishing MPA core | 30 | Statutory prohibition, not a weather hazard — but it carries the same real consequence for the fisher |

## Confidence is not risk

They are separate numbers and the UI shows both.

- **Risk score** — how dangerous the measured conditions are.
- **Confidence** — how much of the picture we actually obtained:
  `confidence = clamp(dataCompleteness − Σ upstream penalties, 0.1, 1.0)`

`dataCompleteness` is the fraction of the three data agents (weather, ocean, geospatial) that
returned. Penalties: **0.2** for an agent that fell back to a cached snapshot, **0.35–0.4** for
one that failed outright.

A high-confidence `SAFE` and a low-confidence `SAFE` mean very different things, so they never
collapse into one number.

## Cleared checks

The gauge lists every rule that was evaluated *and passed*, with its measured value beside its
threshold. This is what makes the assessment auditable: it proves the engine checked the whole
table, not only the items it chose to report.

## Threshold provenance — read this before citing it

The thresholds above encode operational practice for Indian coastal waters: INCOIS high-wave
alert bands, IMD fishermen-warning wind speeds, Beaufort-scale small-craft conventions and
the MHA/Coast Guard boundary buffers. They are the right *shape* and the right order of
magnitude, and the engine is built so they are all in one table.

They are **not** transcribed from a specific gazetted bulletin. Before any operational
deployment each row must be pinned to the exact current INCOIS/IMD definition, reviewed with
the relevant state fisheries department, and versioned — which is why the engine emits its
version string (`orca-deterministic-rules-v1`) with every assessment.

## Changing a threshold

Edit `THRESHOLDS` in `src/agents/risk.ts`, update the row above, and bump `ENGINE_VERSION`.
The version travels with every answer and appears in the UI, so an assessment can always be
traced back to the rule set that produced it.

---

# Vessel recall triage

Solver: `orca-triage-solver-v1` · Source: [`src/agents/triage.ts`](src/agents/triage.ts),
[`src/lib/cyclone.ts`](src/lib/cyclone.ts)

Deterministic, like the hazard engine. No language model touches any number here.

## The quantity being optimised

```
margin = time_until_damaging_wind_reaches_the_vessel
       − time_the_vessel_needs_to_reach_a_safe_harbour
```

Recall order is ascending margin. This is not the same as distance-to-storm: a slow boat far
from the storm with no harbour to run to is in more danger than a fast boat close to it sitting
next to a harbour.

## Priority grouping

Margin alone is undefined for two real cases, so results are grouped before being ordered.
No sentinel values.

| Group | Case | Ordered by |
|---|---|---|
| 0 | Already inside the damaging-wind radius | immediate — distress response, not recall |
| 1 | Cannot reach **any** harbour in time | soonest hazard arrival (rescue window) |
| 2 | Can reach a harbour | smallest margin (tightest recall) |
| 3 | Storm never reaches them on this track | closest approach |

## Damaging-wind radius by intensity

The gale-force (≥ 60 km/h) footprint, which is the band that capsizes an open boat, not the eye.

| Max sustained wind | Radius | Class |
|---|---|---|
| ≥ 165 km/h | 200 nm | Very Severe Cyclonic Storm |
| ≥ 120 km/h | 160 nm | Severe Cyclonic Storm |
| ≥ 88 km/h | 130 nm | Cyclonic Storm |
| ≥ 62 km/h | 100 nm | Deep Depression |
| < 62 km/h | 80 nm | Depression |

Deliberately parametric. GDACS publishes wind-radii polygons per advisory but only sparsely
(Cyclone Dana episode 8 carries one per band for the whole event), so a documented radius beats
interpolating a single polygon across three days.

Arrival time is computed by walking the timestamped track and linearly interpolating within the
segment where distance-to-centre crosses the radius, giving sub-6-hour resolution from
6-hourly advisory points.

## Sea-state derating of vessel speed

A small boat punching into a head sea cannot make its rated cruise speed; the skipper throttles
back against slamming. Applied multiplicatively.

| Significant wave height | Factor | Condition |
|---|---|---|
| ≥ 4.0 m | 0.35 | very rough, barely able to make way |
| ≥ 3.0 m | 0.50 | rough, heavy slamming |
| ≥ 2.0 m | 0.65 | moderate-rough, throttled back |
| ≥ 1.5 m | 0.80 | moderate |
| < 1.5 m | 1.00 | slight |

Sea state is sampled at the **decision time**, not the storm peak, because that is what governs
the run home. For a replay this comes from the real archived wave reanalysis for that date.

## Triage bands

| Margin | Band |
|---|---|
| already exposed | `CRITICAL` |
| < 2 h | `CRITICAL` |
| < 6 h | `URGENT` |
| < 12 h | `WATCH` |
| ≥ 12 h, or storm never arrives | `CLEAR` |

A **safety factor of 1 h** is added to every run time before a harbour is called reachable.

## Harbour selection

Every harbour in the gazetteer is evaluated for every vessel, not just the nearest, because the
shortest run can head into the storm. A harbour is eligible only if the run fits inside **both**
the vessel's own exposure window **and** the harbour's, with the safety factor applied.

### Measured result: diversion almost never helps

Across every advisory timestamp of Cyclone Dana with a 250-vessel fleet, **zero** vessels
benefited from diverting to a harbour other than their nearest.

That is a finding, not a disabled feature. At 2–5 kn effective speed in 3–4 m seas, a boat whose
nearest harbour is inside the storm window cannot reach a farther one either. The decision is
**go now or do not go**, not choose-another-harbour. The logic stays because it will fire for
faster vessels and weaker systems.

## Measured result: the cost of deciding late

Same fleet, same storm, solved at every advisory. Cyclone Dana, 250 simulated vessels,
160 nm damaging-wind radius:

| Decision time | Boats with no reachable harbour |
|---|---|
| T−42 h | **20** |
| T−36 h | 36 |
| T−30 h | 60 |
| T−24 h | 82 |
| T−18 h | **91** |
| T−12 h | 91 |
| T−0 h (landfall) | 91 |

**Waiting from T−42 h to T−18 h strands 71 more boats.** This is the single most useful number
the system produces, and it converts "issue the recall early" from advice into a quantity.
