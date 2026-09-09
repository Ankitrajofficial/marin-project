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
