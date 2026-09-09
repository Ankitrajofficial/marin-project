/**
 * ORCHESTRATOR
 *
 * Runs the planner's execution plan, collects every agent's structured output, and streams
 * progress events to the client as an async generator. Three properties matter:
 *
 *  1. PROGRESSIVE. Events are yielded the moment they happen, so the trace pane fills in
 *     live rather than appearing all at once when the request completes.
 *  2. GRACEFUL DEGRADATION. A failing agent never aborts the run. Its confidence penalty is
 *     subtracted, its absence is recorded in `missing`, and the answer says so out loud.
 *  3. PARALLEL WHERE HONEST. The ocean and weather agents are independent, so they run
 *     concurrently and their step_end events are emitted in completion order.
 *
 * Production equivalent: a LangGraph StateGraph with the same nodes and edges.
 */
import { runPlannerAgent } from "./planner";
import { runWeatherAgent } from "./weather";
import { runOceanAgent } from "./ocean";
import { runGeospatialAgent } from "./geospatial";
import { runRiskAgent } from "./risk";
import { runRouteAgent } from "./route";
import { runSynthesisAgent } from "./synthesis";
import { llmAvailable } from "@/lib/llm";
import { getScenario } from "@/lib/scenarios";
import type {
  AgentResult, GeospatialData, MapPayload, OceanData, Provenance, QueryPlan,
  RouteData, StreamEvent, WeatherData, Lang, ArchiveWindow,
} from "@/lib/types";

export interface OrchestrationRequest {
  query: string;
  language?: Lang | null;
  device?: { lat: number; lon: number } | null;
  scenarioId?: string | null;
  /** Test hooks used by the hardening pass. */
  useLlm?: boolean;
  timeoutMs?: number;
  /**
   * Presentation-only delay between streamed events, in ms. The trace is meant to be read
   * by a human across a room; without a small pause the whole run can land in one frame.
   * It delays the DISPLAY of events, never the work, and every card shows its real measured
   * duration. Set to 0 to disable.
   */
  uiPaceMs?: number;
}

const sleep = (ms: number) => (ms > 0 ? new Promise((r) => setTimeout(r, ms)) : Promise.resolve());

export async function* orchestrate(req: OrchestrationRequest): AsyncGenerator<StreamEvent> {
  const runId = `run_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
  const t0 = Date.now();
  const pace = req.uiPaceMs ?? 220;
  const scenario = getScenario(req.scenarioId);
  const archive: ArchiveWindow | undefined = scenario?.archive;
  const opts = { timeoutMs: req.timeoutMs, scenarioId: scenario?.snapshotId, archive };

  yield {
    type: "meta",
    runId,
    query: req.query,
    startedAt: new Date().toISOString(),
    llmAvailable: req.useLlm === false ? false : llmAvailable(),
    scenarioId: scenario?.id,
  };

  try {
    // ---------------------------------------------------------------- 1. PLAN
    yield { type: "step_start", agent: "planner", task: "Decompose the query", index: 0, total: 1 };
    const plannerResult = await runPlannerAgent({
      query: req.query,
      device: req.device,
      forcedLanguage: req.language ?? null,
      useLlm: req.useLlm,
    });
    const plan = plannerResult.data as QueryPlan;

    // A historical replay overrides the parsed time window with the event's own window.
    if (archive) {
      plan.timeWindow = {
        label: archive.label,
        labelKey: "",
        startIST: `${archive.startDate}T00:00`,
        endIST: `${archive.endDate}T23:00`,
        hoursAhead: 0,
      };
    }

    yield { type: "step_end", result: plannerResult };
    await sleep(pace);
    yield { type: "plan", plan, durationMs: plannerResult.durationMs };
    if (plannerResult.error) yield { type: "notice", level: "warn", message: plannerResult.error };
    await sleep(pace);

    const dataSteps = plan.steps.filter((s) => s.agent !== "risk" && s.agent !== "synthesis");
    const total = plan.steps.length;
    let index = 1;

    const provenance: Provenance[] = [...plannerResult.provenance];
    const agentProvenance: Partial<Record<AgentResult["agent"], Provenance[]>> = {
      planner: plannerResult.provenance,
    };
    const missing: string[] = [];
    let upstreamPenalty = 0;
    let weather: WeatherData | null = null;
    let ocean: OceanData | null = null;
    let geo: GeospatialData | null = null;
    let route: RouteData | null = null;

    const absorb = (r: AgentResult) => {
      provenance.push(...r.provenance);
      agentProvenance[r.agent] = [...(agentProvenance[r.agent] ?? []), ...r.provenance];
      upstreamPenalty += r.confidencePenalty;
      if (!r.ok) missing.push(r.agent);
    };

    // ------------------------------------------------- 2. GEOSPATIAL (local, instant)
    const geoStep = dataSteps.find((s) => s.agent === "geospatial");
    if (geoStep) {
      yield { type: "step_start", agent: "geospatial", task: geoStep.task, index: index++, total };
      const r = runGeospatialAgent(plan.location);
      geo = r.data;
      absorb(r);
      await sleep(pace);
      yield { type: "step_end", result: r };
      await sleep(pace);
    }

    // ------------------------------------------------- 3. OCEAN + WEATHER (parallel)
    const oceanStep = dataSteps.find((s) => s.agent === "ocean");
    const weatherStep = dataSteps.find((s) => s.agent === "weather");

    type Settled = { r: AgentResult; key: symbol };
    const inflight = new Map<symbol, Promise<Settled>>();
    const launch = (agent: "ocean" | "weather", p: Promise<AgentResult>) => {
      const key = Symbol(agent);
      inflight.set(key, p.then((r) => ({ r, key })));
    };

    if (oceanStep) {
      yield { type: "step_start", agent: "ocean", task: oceanStep.task, index: index++, total };
      launch("ocean", runOceanAgent(plan.location, plan.timeWindow, opts) as Promise<AgentResult>);
    }
    if (weatherStep) {
      yield { type: "step_start", agent: "weather", task: weatherStep.task, index: index++, total };
      launch("weather", runWeatherAgent(plan.location, plan.timeWindow, opts) as Promise<AgentResult>);
    }

    // Emit each result the instant it settles, not when the slowest one finishes.
    while (inflight.size) {
      const { r, key } = await Promise.race(inflight.values());
      inflight.delete(key);
      if (r.agent === "ocean") ocean = r.data as OceanData | null;
      if (r.agent === "weather") weather = r.data as WeatherData | null;
      absorb(r);
      yield { type: "step_end", result: r };
      await sleep(pace);
    }

    // ------------------------------------------------- 4. ROUTE (optional)
    const routeStep = dataSteps.find((s) => s.agent === "route");
    if (routeStep && plan.destination) {
      yield { type: "step_start", agent: "route", task: routeStep.task, index: index++, total };
      const r = await runRouteAgent(plan.location, plan.destination, plan.timeWindow, opts);
      route = r.data;
      absorb(r);
      yield { type: "step_end", result: r };
      await sleep(pace);
    }

    // ------------------------------------------------- 5. RISK (deterministic, no LLM)
    const riskStep = plan.steps.find((s) => s.agent === "risk")!;
    yield { type: "step_start", agent: "risk", task: riskStep.task, index: index++, total };
    const riskResult = runRiskAgent({ weather, ocean, geo, upstreamPenalty, missing });
    const risk = riskResult.data!;
    provenance.push(...riskResult.provenance);
    agentProvenance.risk = riskResult.provenance;
    await sleep(pace);
    yield { type: "step_end", result: riskResult };
    yield { type: "risk", risk };
    await sleep(pace);

    // ------------------------------------------------- 6. MAP
    yield { type: "map", map: buildMap(plan, geo, route, scenario?.zoom) };
    await sleep(pace);

    // ------------------------------------------------- 7. SYNTHESIS
    const synthStep = plan.steps.find((s) => s.agent === "synthesis")!;
    yield { type: "step_start", agent: "synthesis", task: synthStep.task, index: index++, total };
    const synthResult = await runSynthesisAgent({
      plan, weather, ocean, geo, risk, route, provenance, agentProvenance, missing, archive, useLlm: req.useLlm,
    });
    yield { type: "step_end", result: synthResult };
    yield { type: "answer", answer: synthResult.data! };

    yield {
      type: "done",
      totalMs: Date.now() - t0,
      degradedAgents: missing,
    };
  } catch (err) {
    yield {
      type: "error",
      message: err instanceof Error ? `${err.name}: ${err.message}` : String(err),
    };
    yield { type: "done", totalMs: Date.now() - t0, degradedAgents: ["orchestrator"] };
  }
}

function buildMap(
  plan: QueryPlan,
  geo: GeospatialData | null,
  route: RouteData | null,
  zoomOverride?: number,
): MapPayload {
  const markers: MapPayload["markers"] = [
    {
      lat: plan.location.lat, lon: plan.location.lon, kind: "query",
      label: plan.location.name,
      detail: `${plan.location.lat.toFixed(4)}, ${plan.location.lon.toFixed(4)} · ${plan.location.resolvedBy}`,
    },
  ];

  if (plan.destination) {
    markers.push({
      lat: plan.destination.lat, lon: plan.destination.lon, kind: "destination",
      label: plan.destination.name,
    });
  }
  if (geo?.nearestHarbour && geo.nearestHarbour.distanceKm > 1) {
    markers.push({
      lat: geo.nearestHarbour.lat, lon: geo.nearestHarbour.lon, kind: "harbour",
      label: geo.nearestHarbour.name,
      detail: `Nearest harbour of refuge · ${geo.nearestHarbour.distanceKm} km`,
    });
  }
  if (geo?.nearestPfz) {
    markers.push({
      lat: geo.nearestPfz.centroid[1], lon: geo.nearestPfz.centroid[0], kind: "pfz",
      label: `PFZ · ${geo.nearestPfz.distanceKm} km`,
      detail: `Demo derivation, not an INCOIS advisory · SST ${geo.nearestPfz.sstC} °C`,
    });
  }
  if (geo?.boundary && geo.boundary.severity !== "clear") {
    markers.push({
      lat: geo.boundary.nearestPoint[1], lon: geo.boundary.nearestPoint[0], kind: "hazard",
      label: `${geo.boundary.distanceNm} nm to boundary`,
      detail: geo.boundary.name,
    });
  }

  const activeLayers = ["coastline", "harbours"];
  if (plan.intent === "pfz" || plan.intent === "productivity") activeLayers.push("pfz");
  if (plan.intent === "geofence" || (geo?.boundary && geo.boundary.severity !== "clear")) activeLayers.push("imbl", "mpa");
  if (plan.intent === "route") activeLayers.push("route");
  if (!activeLayers.includes("pfz")) activeLayers.push("pfz");
  if (!activeLayers.includes("imbl")) activeLayers.push("imbl");

  const zoom = zoomOverride ?? (plan.destination ? 7 : plan.intent === "geofence" ? 9 : 8);

  return {
    center: [plan.location.lat, plan.location.lon],
    zoom,
    markers,
    activeLayers: [...new Set(activeLayers)],
    highlightPfzIds: geo?.nearestPfz ? [geo.nearestPfz.id] : [],
    route: route
      ? { direct: route.directPolyline, recommended: route.recommendedPolyline, waypoints: route.waypoints }
      : undefined,
    boundaryWarning:
      geo?.boundary && geo.boundary.severity !== "clear"
        ? { name: geo.boundary.name, distanceNm: geo.boundary.distanceNm, severity: geo.boundary.severity }
        : undefined,
  };
}
