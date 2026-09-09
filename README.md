# ORCA — Agentic AI Marine Intelligence Platform

**Smart India Hackathon 2026 · Problem Statement 26176 (ISRO) · Theme: Disaster Management**
Team NextGen Coder · Team ID SIH-S-B1-212

A conversational platform where a fisherman, coastal officer or researcher asks a question in
natural language and gets an evidence-backed answer built by several specialised AI agents
working together — with the reasoning visible while it happens.

## Run it

```bash
npm install
npm run dev          # http://localhost:3000
```

**No environment variables are required.** With no API key the planner uses a deterministic
rule-based parser and answers are composed from templates in all six languages. Everything
works, including all six demo scenarios.

Optionally, for more fluent prose and better handling of off-script questions:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env.local
```

The key changes *how the answer is phrased* and *how the query is parsed*. It never changes the
safety verdict — see below.

## What is real and what is simulated

This is the section to read first, and it is the same thing the UI says on screen.

### Real, live, fetched while you watch

| Data | Source | Notes |
|---|---|---|
| Significant wave height, direction, period | Open-Meteo Marine API | free, keyless |
| Swell height and period | Open-Meteo Marine API | free, keyless |
| Sea surface temperature | Open-Meteo Marine API | free, keyless |
| Wind speed, gusts, direction | Open-Meteo Forecast API | free, keyless |
| Rainfall, weather code, visibility | Open-Meteo Forecast API | free, keyless |
| Cyclone Dana conditions (scenario 2) | Open-Meteo historical archive (ERA5 + wave reanalysis) | real measured conditions from a real cyclone, fetched at demo time, labelled as a **historical replay** |

Hourly forecasts run 3 days ahead, so "tomorrow morning" queries resolve against a genuine
06:00–12:00 IST window.

### Cached demo layers, labelled `CACHED` everywhere they appear

| Layer | What it actually is |
|---|---|
| **Potential Fishing Zones** | An **ORCA demo derivation** from a static SST + chlorophyll-a proxy table, using a simplified version of the thermal-front + productivity logic INCOIS uses. **These are not INCOIS advisories and are never presented as such.** Production consumes the INCOIS PFZ advisory feed. The derivation is reproducible: `node scripts/generate-layers.mjs` |
| **Maritime boundaries (IMBL)** | Approximate turning points digitised from public descriptions of the 1974/1976 India–Sri Lanka agreements, plus an indicative line in the disputed Sir Creek sector. Accurate to roughly 2–5 km. **Must not be used for navigation.** |
| **Marine protected areas** | Five simplified rectangles approximating real gazetted MPAs. Indicative extents, not gazetted boundaries. |
| **Coastline / EEZ** | Hand-digitised at ~50 km resolution; the EEZ is the coastline offset 200 nm. Cartographic context only, not a legal boundary. |
| **Harbours** | 20 real fishing harbours and landing centres, compiled from public listings. Used for text→coordinate resolution. |

### Not implemented (roadmap, honestly labelled)

Bhashini ASR/TTS/translation · real SMS/IVR delivery · offline-at-sea bundle · React Native
app · PostGIS/H3 · LangGraph · user accounts. See ARCHITECTURE.md for where each one plugs in.

**Nothing cached is ever presented as live.** Every value in the UI carries a provenance chip
showing its source, whether it is `LIVE` / `CACHED` / `ARCHIVE` / `SNAPSHOT` / `DERIVED`, and
its computed age.

## The three things this proves

### 1. Real multi-agent orchestration, visible

Seven separate modules with their own inputs, tools and outputs, coordinated by a planner —
not one LLM call pretending to be five agents. The centre pane streams each step live: which
agent, what it was asked, which API it called, what came back, how long it took. Every card
expands to the **raw JSON from the real API**.

The planner's decomposition streams *first*, before any agent runs, so you can see the query
being broken into sub-tasks.

### 2. Real data

Live wave height, swell, wind and SST for any Indian coastal coordinate, fetched at demo time
from two free keyless APIs. Expand a trace card and the numbers on screen are in the payload.

### 3. Explainability

Every answer carries its evidence list (each fact tagged with the agent that produced it and
that agent's source), its data age, its confidence, and the specific hazard rules that fired
with **measured value beside threshold** — "wave height 4.18 m vs ≥ 2.5 m", not a bare number.

## The hazard engine does not use an LLM

This is the design decision worth defending.

`src/agents/risk.ts` is a deterministic rule engine. Same inputs, same output, every time.
Every threshold is documented in [RISK-RULES.md](RISK-RULES.md) with its justification. The
language model plans the query and writes the explanation; **it never decides whether it is
safe to go to sea.**

It also refuses to be reassuring without data: if the ocean or weather agent fails, the band
becomes `UNKNOWN`, not `SAFE`. A score of 0 means "no hazard found in the data we have", which
is not the same as "it is safe", and the system says so.

## Demo scenarios

Six pinned scenarios, each on a real Indian coastal location. Every one runs the **real
pipeline against the live APIs**; if the network is unavailable each falls back to a snapshot
captured from a real prior response, always visibly labelled.

| # | Scenario | Location |
|---|---|---|
| 1 | Safe fishing day | Kanyakumari |
| 2 | Cyclone warning — **real Cyclone Dana archive replay** | Paradip, Odisha |
| 3 | Nearest PFZ | Chennai |
| 4 | Geofence proximity warning | Off Rameswaram, 2.2 nm from the India–Sri Lanka IMBL |
| 5 | Safe route | Chennai → Puducherry |
| 6 | Productivity decline | Kerala coast, off Kochi |

Scenario 2 deserves a note. Today's Odisha coast is usually calm, so rather than invent a
cyclone, ORCA replays **real archived conditions from Cyclone Dana's landfall (24–25 Oct
2024)** — 4.18 m waves, 48.9 km/h sustained wind, 88.9 km/h gusts, 164 mm rain — pulled live
from the Open-Meteo historical archive and labelled as a historical replay in the answer, on
the provenance chips, and in the time-window field.

## Languages

English, Hindi, Tamil, Bengali, Malayalam, Telugu. Query language is auto-detected by script
and the answer is returned in the same language: verdict, sea state, wind, hazard rule names,
weather conditions and time windows are all localised (158 keys per locale, parity-checked).

Voice input and read-aloud use the browser Web Speech API — free, no service, no key.

This is a **Bhashini-compatible layer**. Bhashini is *not* integrated; production swaps it in
at the same seam.

## Keyboard

| Shortcut | Action |
|---|---|
| `Ctrl`/`Cmd` + `Shift` + `K` | Hidden demo reset — clears conversation, trace, map and server cache instantly, no page reload |
| `Enter` | Send · `Shift`+`Enter` for a newline |

## Verify it yourself

```bash
npx tsc --noEmit     # clean
npx next build       # clean
node scripts/generate-layers.mjs   # regenerates the PFZ / coastline / EEZ layers
```

## Stack

Next.js 15 (App Router) · React 19 · TypeScript strict · Tailwind CSS v4 · Leaflet +
react-leaflet (free, no token) · Turf.js · Anthropic SDK (optional).
No database. No Python backend. Deploys to Vercel free tier.

Chosen for build speed, not because it is the production stack — see
[DECISIONS.md](DECISIONS.md) for every substitution and why.

## Documents

- [ARCHITECTURE.md](ARCHITECTURE.md) — agent diagram, orchestration flow, prototype→production mapping
- [RISK-RULES.md](RISK-RULES.md) — every hazard threshold with its justification
- [DECISIONS.md](DECISIONS.md) — every autonomous choice made while building this

## Safety notice

ORCA is a hackathon prototype. It is **not** a navigational aid and must not be used to make
real decisions at sea. Boundary positions are approximate. Fishing zones are a demo derivation,
not an INCOIS advisory. Always follow official INCOIS, IMD and Coast Guard advisories.
