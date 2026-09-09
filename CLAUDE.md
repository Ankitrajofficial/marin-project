# ORCA — SIH 2026, PS 26176
Team NextGen Coder (SIH-S-B1-212). Theme: Disaster Management.

## What this is
A marine hazard decision engine: risk field, safe routes, and vessel
recall priority computed from fused multi-source ocean data.

## What this is NOT
Not a portal aggregator, not a chatbot with a map. "All marine data in
one place" is not a differentiator — INCOIS Sagar Vani already does
multilingual advisory dissemination. The algorithms in core/ are the
product.

## The one hard rule
The LLM never generates a number. Every number comes from a
deterministic solver in backend/app/core/. The LLM only picks which core
function to call, and reads the result back in plain language. Every
number is logged in `traces` with its source id and data-age.

## Scenario data (cyclone demo) — not built yet, build it this way
Synthetic data lives in backend/app/scenarios/, never in core/ and never in
adapters/. It writes into observations under source_id 'scenario_sim'.
core/ stays untouched and cannot tell scenario rows from real ones except by
source_id, which is exactly the point.

Reliability 0.90, NOT 0.0. Reliability answers "how much do I believe this
source about the real world"; a scenario's job is to REPLACE the real world.
Different axes. At 0.0 the reliability-weighted fusion gives scenario rows
zero weight, so an injected cyclone sitting alongside real calm data
contributes nothing and the hazard field stays calm — the demo silently
shows no storm. Visibility comes from source_id in the trace, not from
down-weighting.

Within a scenario the simulated world is the only world: the scenario path
MASKS overlapping real observations for its window and AOI. Never blend a
real calm sea with an injected cyclone — a half-real half-simulated hazard
field is not a forecast of anything.

It must not be reachable from the normal ingest path: no scenario import in
jobs/ingest_*.py, no flag on a real adapter.

Hard guard, already enforced in core/risk.py: any risk_cells row whose
inputs include a scenario source is written with simulated = true, and the
same must hold for traces. The API must surface that flag on every response
that carries a simulated number, so the frontend can badge it. A simulated
result must never be presentable as a real forecast.

## Layers (data flows bottom-up)
1. adapters/ — one translator per source. Fetch, normalize into ONE
   Observation shape, insert. Zero computation here.
2. core/ — the actual contribution:
   fusion.py   co-register sources onto common H3 cell + time bin,
               resolve conflicts via per-source reliability priors,
               propagate uncertainty
   gapfill.py  DINEOF reconstruction of cloud-gapped SST/CHL — answers
               where INCOIS' own product goes blank in monsoon
   risk.py     probabilistic hazard field: exceedance probability per
               cell per time step from forecast spread. NOT a weighted sum
   routing.py  time-dependent A* over a moving hazard field
   recall.py   vessel triage: rank by (time-to-safe-harbour minus
               time-to-hazard-arrival), smallest margin first
3. agents/ — LangGraph orchestration, tool-calling only
4. api/ — FastAPI routes
5. frontend/ — Next.js + MapLibre + OSM tiles (no Mapbox, no map key)

## Primary demo
Ask a question in plain language and get an answer whose every number is
traceable: "is it safe to go out tomorrow morning near Nagapattinam?" ->
exceedance probabilities from core/risk.py over that cell and window, with
sources, data age and uncertainty shown. "How close am I to the boundary?" ->
deterministic geofencing against the India-Sri Lanka IMBL. The reasoning trace
is part of the answer, not a debug view: it is what makes the number checkable.

The disaster-management extension on top: cyclone scenario, N boats at sea,
a prioritized recall list for the coastal authority and a safe route per boat.
Same engine, same trace, higher stakes. PFZ fishing advisory is a side output
of the same engine, not a separate product.

## Stack
PostgreSQL 16 + PostGIS + TimescaleDB + pgvector. One database, no Redis
in MVP. Python 3.11, FastAPI, LangGraph. Next.js + MapLibre.

## Sources by access reality
Tier 1 (no key, works today): Open-Meteo Marine + Weather, AISStream,
OSM Overpass, NOAA ERDDAP, GEBCO.
Tier 2 (registration): Copernicus Marine, Bhuvan, MOSDAC, Bhashini, IMD.
Tier 3 (no API, bulletin/scrape): INCOIS PFZ, INCOIS OSF.
The adapter layer exists because these tiers differ in format, units,
time convention and access model. That normalization is a feature.

## Boundaries are advisory, never legal
Geofencing uses open data: MarineRegions/VLIZ (CC-BY 4.0) for the IMBL, EEZ,
territorial sea and baselines; OpenStreetMap (ODbL) for marine protected
areas. Neither is Survey of India, neither carries legal authority, and India
regulates how its boundaries may be depicted. WDPA/Protected Planet is the
proper MPA source but needs a token (Tier 2) — swapping it in means replacing
zones/osm_protected.py and nothing else.

hazard_zones.authority is NOT NULL, and every geofence response carries
advisory_only and attributions as REQUIRED fields — same enforcement pattern
as `simulated`. No map, panel, or agent sentence may present these lines as
the legal boundary.

Verdicts apply an explicit uncertainty margin (boundary-data error + vessel
position error) so "clear" means clear BY A MARGIN. The target is zero false
negatives on the IMBL: a false positive costs a course change, a false
negative costs a boat and its crew's liberty.

## Build order — do not jump ahead
1 db schema + docker
2 adapters/base.py (the Observation contract)
3 adapters/open_meteo.py + ingest job
4 core/grid.py + core/risk.py
5 api + map (first visible output)
6 zones/ + core/geofence.py — IMBL, MPA, restricted waters. Explicit PS
  requirement, and routing needs it as a hard constraint: a route that
  crosses the IMBL is worse than no route.
7 agents/ — the conversational layer. Moved ahead of recall because it is
  the heart of the PS: the LLM plans and verbalizes, core/ produces every
  number, guards reject any figure not present in a tool result.
8 adapters/aisstream.py + core/recall.py
9 core/routing.py
10 core/gapfill.py

## How to work with me
Plan first, wait for approval, then write. One build step at a time.
Never mock data inside core/. Comment core/ heavily — judges read it.
Ask before adding any dependency.
