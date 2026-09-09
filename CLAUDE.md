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
Cyclone scenario, N boats at sea. Output = prioritized recall list for
the coastal authority, a safe route per boat, and a reasoning trace
showing which data was used and how old it was. PFZ fishing advisory is
a side output of the same engine, not the main product.

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

## Build order — do not jump ahead
1 db schema + docker
2 adapters/base.py (the Observation contract)
3 adapters/open_meteo.py + ingest job
4 core/grid.py + core/risk.py
5 api + map (first visible output)
6 adapters/aisstream.py + core/recall.py
7 core/routing.py
8 core/gapfill.py
9 agents/ — last, demo works without it

## How to work with me
Plan first, wait for approval, then write. One build step at a time.
Never mock data inside core/. Comment core/ heavily — judges read it.
Ask before adding any dependency.
