# Decisions

Every autonomous choice made while building this prototype, and why. Where a choice replaces
something from the deck, the production equivalent is named.

---

## Stack

**Next.js 15 route handlers instead of FastAPI + LangGraph.**
One language, one process, one deploy, and SSE streaming with no extra plumbing. The
orchestrator is an async generator whose nodes and edges map 1:1 onto a LangGraph `StateGraph`,
so the port is mechanical rather than a rewrite.

**No database.** Layers are GeoJSON in `/data`; conversation state lives in React; response
caching is a process-local `Map` with a 10-minute TTL. Data volumes here are tiny. Production
uses PostGIS + H3 for spatial work and Redis for caching, with the same cache key shape.

**Leaflet + CARTO dark basemap, not Mapbox.** Mapbox needs a token. A demo that dies because a
token expired is a failed demo. CARTO's dark tiles are free, keyless, and suit the dark theme.

**Turf.js for all geodesy.** `distance`, `nearestPointOnLine`, `booleanPointInPolygon` cover
every spatial question asked here. The geospatial agent is the only file touching the layers,
so swapping in PostGIS touches one module.

---

## Data

**Open-Meteo for live ocean and weather.** Free, keyless, no registration, hourly forecasts 3
days out, and both a marine and an atmospheric endpoint. INCOIS and MOSDAC are the production
sources; Open-Meteo is what a team can genuinely demo on venue wifi today, and it stays as
redundancy later.

**Open-Meteo's multi-coordinate mode for routing.** Both endpoints accept comma-separated
coordinate lists and return an array. A 9-waypoint route costs 2 HTTP requests instead of 18.

**PFZ zones are a documented derivation, not fabricated polygons.**
`scripts/generate-layers.mjs` computes them from a static SST + chlorophyll-a proxy table:

```
score = 0.45 · norm(sst_gradient, 0.15…0.65) + 0.55 · norm(chlorophyll_a, 0.6…2.8)
emit zones with score ≥ 0.35
```

This is the *shape* of INCOIS PFZ logic — thermal fronts plus primary productivity — at toy
fidelity. The script is committed so the derivation is inspectable and reproducible. Labelled
"Demo derivation — production consumes the INCOIS PFZ advisory feed" in the layer metadata, in
every map popup, in the answer text and on the provenance chip. **Never called an advisory.**

*Tuning note:* the first run emitted only 5 zones, all on the Kerala–Gujarat upwelling belt,
leaving the entire east coast — including Chennai, needed for scenario 3 — empty. The east
coast anchors' chlorophyll values were raised to reflect real Ganga/Mahanadi/Godavari plume
enrichment and the emit threshold lowered to 0.35, giving 14 zones with sensible coverage.
Rameswaram still emits none, which is correct: Palk Bay is not a productive fishing front, and
it makes scenario 4's "no zone here, and you are near the boundary" story land properly.

**Boundaries are approximate and say so loudly.** The India–Sri Lanka IMBL turning points are
digitised from public descriptions of the 1974/1976 agreements. The Sir Creek sector has no
delimitation agreement in force, so it is labelled disputed and indicative. Every popup and
every answer that mentions a boundary repeats that these positions must not be used for
navigation.

**Cyclone scenario replays a real cyclone rather than inventing one.**
The Odisha coast is usually calm, so a live query at Paradip would return `SAFE` and the
"cyclone warning" scenario would be a lie. Instead ORCA fetches **real archived conditions from
Cyclone Dana's landfall (24–25 Oct 2024)** from the Open-Meteo historical archive at demo
time — 4.18 m waves, 48.9 km/h wind, 88.9 km/h gusts, 164 mm rain. This is genuine measured and
reanalysed data from a genuine cyclone. It is labelled a historical replay in the answer, on
the provenance chips (`ARCHIVE`, stamped with the *event* date so the age chip reads "686 d
old"), and in the time-window field. Dramatic *and* defensible; a fabricated cyclone would be
neither.

**Offline snapshots are verbatim captures, refused beyond 250 km.**
Snapshots exist for one reason: a venue network that dies mid-demo. They are only used *after*
a live call has actually failed, always surfaced as `SNAPSHOT` with their capture time, and a
snapshot more than 250 km from the query point is refused — answering a Kochi question with
Chennai's sea state would be worse than admitting the failure. The archive snapshot is never
auto-substituted for live conditions.

---

## Agents

**Seven separate modules, not one prompt.** Each has its own inputs, tools, outputs and
provenance. The trace pane is only convincing because there is genuinely something to show.

**The risk engine never uses an LLM, and that is the whole point.**
`risk.ts` is a pure function. Same inputs, same output. Every threshold documented in
RISK-RULES.md. This answers the hallucination objection structurally rather than rhetorically.

**`UNKNOWN` band added after testing.** A hardening test knocked out both data agents and the
engine returned `0/100 SAFE` — "conditions look safe for a small fishing vessel", backed by no
data at all. That is the most dangerous output this system could produce. The engine now
refuses to issue a reassuring band without both sea state and wind. Observed hazards still
escalate normally; only the *reassurance* is withheld.

**The LLM names places; it never supplies coordinates.** The planner may extract "Kanyakumari"
from a query, but the coordinate always comes from the bundled harbour gazetteer or from
explicit user input. A model cannot invent a position.

**Rule-based fallbacks are mandatory, not decorative.** Both LLM-using agents have a
deterministic path that runs with zero configuration, and both are exercised by default because
no key is set in development. A demo that dies because a key is missing is a failed demo.

**Ocean and weather run in parallel, and stream in completion order.** A keyed `Promise.race`
emits each result the instant it settles, so the trace shows whichever genuinely finished
first — not a fixed order.

**Route intent is inferred from resolved endpoints, not just keywords.** An early keyword list
classified "Is it safe to sail from Chennai?" as a routing query because it contained "sail
from". Keywords were narrowed, and any query that resolves *two* harbours is now treated as a
passage question regardless of phrasing.

**No geocoding dependency.** Locations resolve against the 20-harbour gazetteer, explicit
`lat, lon` coordinates, or a device fix — with an explicit notice when it falls back to the
default. A geocoding API is one more thing to fail on venue wifi.

---

## UI

**Three panes, centre pane widest.** The reasoning trace is the differentiator, so it gets the
most space and the most design attention.

**Presentation pacing (`uiPaceMs`, default 220 ms).** Real pipeline runs complete in ~250 ms,
which lands the whole trace in a single frame and destroys the effect the pane exists to
create. This delay affects **display only** — never the work — and every card reports its own
real measured duration. Set `uiPaceMs: 0` to disable.

**Raw payloads are expandable everywhere.** A judge who clicks in must find real JSON from a
real API. This is the credibility moment and it costs one click.

**Provenance chips are a designed feature, not a disclaimer.** The deck promises a data-age
stamp on every answer, so age is *computed* from `fetchedAt` and rendered beside every value.

**Cleared checks are shown, not just triggered rules.** Listing what passed proves the engine
evaluated the whole table.

**Font sizes raised for a projector.** Trace and answer text were enlarged (13.5 px → 14.5 px
body, 15 px → 16 px agent labels, 34 px → 38 px risk score) for legibility across a room at
1920×1080.

**Rapid clicking cancels the previous run.** Each new query aborts the in-flight request, so
repeated chip clicks cannot stack overlapping streams.

---

## Multilingual

**Six locale files with enforced key parity** (158 keys each, checked in the build notes).
Localised: UI, verdicts, sea state, wind, **hazard rule names**, WMO weather conditions and
time-window labels. Left in English deliberately: harbour proper nouns, upstream source names,
the engine version string, and SI units.

Rule names were localised late, after a Tamil test answer came back ~90% Tamil with English
rule labels in the most important sentence. A judge reading Tamil would have seen exactly that.

**Web Speech API for voice.** Free, no service, no key, and supports Indic language codes where
the browser does. Production swaps in Bhashini ASR/TTS at the same seam.

**Language is auto-detected by script range.** For these six languages the writing systems do
not overlap, so a script check is exact and needs no dependency. An explicit UI selection always
wins over detection, because the user chose it.

---

## Deliberately not done

- **No PWA / service worker.** Offline-at-sea is a real requirement but needs the 24-hour
  bundle design from the deck, not a cache shim that would misrepresent it.
- **No SMS provider.** The alerts panel renders the exact SMS (within the 160-character
  limit), IVR script and push payload, and sends nothing.
- **No user accounts or vessel registry.** Nothing in the demo needs identity.
- **No test suite.** Verification was done by running all six scenarios plus eight failure
  modes end to end against the live server. For a two-day prototype that found more real bugs
  per minute than unit tests would have — including the `SAFE`-without-data bug.
