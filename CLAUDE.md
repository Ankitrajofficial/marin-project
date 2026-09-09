# ORCA — project notes for Claude

Agentic AI Marine Intelligence Platform. SIH 2026, Problem Statement 26176 (ISRO),
Disaster Management. Team NextGen Coder, SIH-S-B1-212.

Next.js 15 App Router · React 19 · TypeScript strict · Tailwind v4 · Leaflet · Turf.js.
No database, no Python backend. Deploys to Vercel.

## Non-negotiables

These are the properties the whole project rests on. Do not break them.

**1. The risk engine never uses an LLM.** `src/agents/risk.ts` is a pure function of measured
values and the `THRESHOLDS` table. Same inputs, same output, every time. A language model plans
the query (`planner.ts`) and phrases the answer (`synthesis.ts`) and does nothing else. If you
add a hazard rule, add it to `THRESHOLDS`, document it in `RISK-RULES.md`, and bump
`ENGINE_VERSION`.

**2. The app must run with zero environment variables.** `ANTHROPIC_API_KEY` is optional. Both
LLM-using agents have a deterministic fallback and it must stay working — it is the path that
gets demoed when venue wifi fails. Test changes with the key unset.

**3. Nothing cached is ever presented as live.** Every rendered value carries a `Provenance`
record with a `kind` (`LIVE` / `CACHED` / `ARCHIVE` / `SNAPSHOT` / `DERIVED`) and a `fetchedAt`
so age is computed, not asserted. If you add a data value, attach provenance.

**4. PFZ polygons are never called an INCOIS advisory.** They are an ORCA derivation from an
SST + chlorophyll proxy (`scripts/generate-layers.mjs`). Every mention in code, data, UI and
docs is either a negation or a production-roadmap reference. Keep it that way.

**5. The engine will not report SAFE without data.** If the ocean or weather agent fails, the
band is `UNKNOWN`, not `SAFE`. A score of 0 means "no hazard in the data we have", which is not
"it is safe".

**6. GDACS is never labelled an IMD alert.** GDACS aggregates NOAA/NHC and JTWC (verified:
every North Indian Ocean event carries `source: "JTWC"`). The official authority for the North
Indian Ocean is RSMC New Delhi (IMD). Show the GDACS track as the machine-readable feed, always
beside a link to the IMD RSMC bulletin, and always with the GDACS attribution string. Enforced
in `src/lib/cyclone.ts`, the triage API response and `RecallPanel.tsx`.

**7. Fleet positions are simulated and must stay labelled.** No public AIS feed exists for
sub-20 m Indian fishing vessels. `src/lib/fleet.ts` is synthetic; everything the solver does with
those positions is real. Never present the fleet as live vessel tracking.

**8. The streaming trace is the differentiator.** Events stream progressively via SSE from an
async generator. Never batch them. `uiPaceMs` delays *display* only — every trace card must
keep reporting its own real measured duration.

## Layout

```
src/agents/      planner · geospatial · ocean · weather · route · risk · triage
                 synthesis · orchestrator
src/lib/         types · layers · geo · net · cache · time · series · provenance
                 llm · i18n · lang · scenarios · snapshots · cyclone · fleet
src/app/api/     query (SSE) · layers · alerts · triage · reset
src/components/  Orca · ConversationPane · TracePane · MapPane · LeafletMap
                 RiskGauge · AlertsPanel · RecallPanel · ProvenanceChip
src/locales/     en · hi · ta · bn · ml · te
data/            harbours · pfz-zones · imbl · mpa · coastline · eez
                 snapshots/ · cyclones/
scripts/         generate-layers.mjs
docs/            ORCA-Technical-Report.md / .pdf
```

Agents share one envelope (`AgentResult` in `src/lib/types.ts`), so nothing downstream knows
whether an LLM was involved.

## Conventions

- All six locales must keep identical key sets. After editing one, verify parity across all six.
- Localise new user-facing strings, including hazard rule names and weather conditions. Proper
  nouns, source names and SI units stay English.
- All reasoning is in IST wall-clock naive ISO strings (`src/lib/time.ts`) because Open-Meteo is
  queried with `timezone=Asia/Kolkata`. Don't introduce `Date` arithmetic across timezones.
- Every outbound fetch goes through `src/lib/net.ts` so it carries a timeout and cannot hang.
- Weather and ocean report the **worst hour** in the window, never the mean.

## Verify

```bash
npx tsc --noEmit                                    # must be clean
npx tsc --noEmit --noUnusedLocals --noUnusedParameters
npx next build                                      # must be clean
node scripts/generate-layers.mjs                    # regenerates derived layers
```

Then run all six demo scenarios against a running server. The scenarios are the real test
suite; they caught every significant bug in this project, including the SAFE-without-data one.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke /office-hours
- Strategy/scope → invoke /plan-ceo-review
- Architecture → invoke /plan-eng-review
- Design system/plan review → invoke /design-consultation or /plan-design-review
- Full review pipeline → invoke /autoplan
- Bugs/errors → invoke /investigate
- QA/testing site behavior → invoke /qa or /qa-only
- Code review/diff check → invoke /review
- Visual polish → invoke /design-review
- Ship/deploy/PR → invoke /ship or /land-and-deploy
- Save progress → invoke /context-save
- Resume context → invoke /context-restore
- Author a backlog-ready spec/issue → invoke /spec
