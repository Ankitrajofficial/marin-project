/** Shared contracts for the ORCA agent mesh. Every agent speaks these types. */

export type Lang = "en" | "hi" | "ta" | "bn" | "ml" | "te";

/** LIVE = fetched from an external API this request. CACHED = shipped file. DERIVED = computed by ORCA. */
export type DataKind = "LIVE" | "CACHED" | "DERIVED" | "SNAPSHOT" | "ARCHIVE";

export interface Provenance {
  /** Human-readable source name shown on the provenance chip. */
  source: string;
  kind: DataKind;
  /** ISO instant the underlying data was obtained. */
  fetchedAt: string;
  /** Seconds between fetchedAt and render time. Drives the "data age" stamp. */
  ageSeconds: number;
  url?: string;
  note?: string;
}

export type AgentName =
  | "planner"
  | "weather"
  | "ocean"
  | "geospatial"
  | "risk"
  | "route"
  | "triage"
  | "synthesis";

export type ToolStatus = "ok" | "error" | "timeout" | "cache" | "fallback";

export interface ToolCall {
  tool: string;
  url?: string;
  params?: Record<string, unknown>;
  status: ToolStatus;
  durationMs: number;
  summary?: string;
  /** Raw payload sample. The trace pane exposes this so a judge can verify it is real. */
  raw?: unknown;
}

export interface AgentResult<T = unknown> {
  agent: AgentName;
  task: string;
  ok: boolean;
  degraded: boolean;
  data: T | null;
  error?: string;
  /** 0..1 subtracted from overall confidence when this agent degrades or fails. */
  confidencePenalty: number;
  provenance: Provenance[];
  toolCalls: ToolCall[];
  durationMs: number;
  startedAt: string;
}

/** A replay of a real past event, fetched live from the Open-Meteo archive at demo time. */
export interface ArchiveWindow {
  startDate: string;
  endDate: string;
  /** Shown verbatim in the UI, e.g. "Cyclone Dana landfall, 24-25 Oct 2024". */
  label: string;
}

export interface AgentOpts {
  timeoutMs?: number;
  scenarioId?: string;
  archive?: ArchiveWindow;
}

export type Intent =
  | "pfz"
  | "safety"
  | "conditions"
  | "alerts"
  | "route"
  | "geofence"
  | "productivity"
  | "unknown";

export interface ResolvedLocation {
  name: string;
  lat: number;
  lon: number;
  /** How the coordinate was obtained. */
  resolvedBy: "harbour-gazetteer" | "explicit-coordinates" | "device" | "default";
  state?: string;
  harbourId?: string;
  /** True when the point is not plausibly marine (inland query guard). */
  inland?: boolean;
}

export interface TimeWindow {
  /** English label, always present. */
  label: string;
  /** Locale key under "window.*" so the answer can render the window in the query language. */
  labelKey: string;
  /** Naive IST wall-clock ISO, e.g. "2026-09-10T06:00". Matches Open-Meteo's timezone=Asia/Kolkata output. */
  startIST: string;
  endIST: string;
  hoursAhead: number;
}

export interface PlanStep {
  agent: AgentName;
  task: string;
  dependsOn: AgentName[];
}

export interface QueryPlan {
  intent: Intent;
  language: Lang;
  location: ResolvedLocation;
  destination?: ResolvedLocation;
  timeWindow: TimeWindow;
  steps: PlanStep[];
  /** "llm" when ANTHROPIC_API_KEY is set and the call succeeded, "rules" otherwise. */
  plannerMode: "llm" | "rules";
  reasoning: string;
  rawQuery: string;
}

// ---------------------------------------------------------------- agent payloads

export interface WeatherData {
  windSpeedKmh: number;
  windGustKmh: number;
  windDirectionDeg: number;
  precipitationMm: number;
  temperatureC: number;
  weatherCode: number;
  weatherLabel: string;
  visibilityM: number | null;
  /** Per-hour series inside the requested window, for the chart and for route sampling. */
  series: Array<{ t: string; wind: number; gust: number; precip: number }>;
  windowLabel: string;
}

export interface OceanData {
  waveHeightM: number;
  waveDirectionDeg: number;
  wavePeriodS: number;
  swellHeightM: number;
  swellPeriodS: number;
  seaSurfaceTempC: number;
  series: Array<{ t: string; wave: number; swell: number }>;
  windowLabel: string;
}

export interface PfzHit {
  id: string;
  nearestHarbour: string;
  distanceKm: number;
  bearingDeg: number;
  centroid: [number, number];
  score: number;
  confidence: string;
  sstC: number;
  chlorophyll: number;
  depthBandM: string;
  label: string;
}

export interface BoundaryHit {
  id: string;
  name: string;
  distanceKm: number;
  distanceNm: number;
  agreement: string;
  accuracy: string;
  severity: "critical" | "warning" | "advisory" | "clear";
  nearestPoint: [number, number];
}

export interface MpaHit {
  id: string;
  name: string;
  inside: boolean;
  distanceKm: number;
  restriction: string;
  rule: string;
  designation: string;
}

export interface GeospatialData {
  nearestPfz: PfzHit | null;
  pfzCandidates: PfzHit[];
  nearestHarbour: { id: string; name: string; distanceKm: number; lat: number; lon: number } | null;
  boundary: BoundaryHit | null;
  boundaries: BoundaryHit[];
  mpa: MpaHit | null;
  mpas: MpaHit[];
  insideEez: boolean;
  distanceToCoastKm: number;
}

export interface TriggeredRule {
  /** Also the locale key: rules.<id>. */
  id: string;
  /** English label, always present as the fallback. */
  label: string;
  /** Substitutions for the localised label (e.g. the boundary name). */
  labelVars?: Record<string, string>;
  measured: number;
  unit: string;
  threshold: number;
  comparator: ">=" | "<=" | "<" | ">";
  severity: "info" | "caution" | "danger";
  points: number;
  source: string;
}

/**
 * UNKNOWN is not a severity - it means the engine could not judge. It exists because
 * reporting SAFE when the ocean and weather agents both failed would be the single most
 * dangerous thing this system could do.
 */
export type RiskBand = "SAFE" | "CAUTION" | "UNSAFE" | "UNKNOWN";

export interface RiskData {
  score: number;
  band: RiskBand;
  rules: TriggeredRule[];
  /** Rules that were evaluated and passed. Shown so the gauge proves it checked everything. */
  cleared: Array<{ id: string; label: string; measured: number; unit: string; threshold: number }>;
  confidence: number;
  dataCompleteness: number;
  missing: string[];
  engine: string;
  vesselClass: string;
}

export interface RouteWaypoint {
  lat: number;
  lon: number;
  index: number;
  riskScore: number;
  band: RiskBand;
  waveHeightM: number | null;
  windKmh: number | null;
  detourReason?: string;
}

export interface RouteData {
  from: ResolvedLocation;
  to: ResolvedLocation;
  directPolyline: Array<[number, number]>;
  recommendedPolyline: Array<[number, number]>;
  waypoints: RouteWaypoint[];
  distanceKm: number;
  worstBand: RiskBand;
  meanRisk: number;
  detoursForced: string[];
}

export interface MapMarker {
  lat: number;
  lon: number;
  kind: "query" | "harbour" | "pfz" | "hazard" | "destination";
  label: string;
  detail?: string;
}

export interface MapPayload {
  center: [number, number];
  zoom: number;
  markers: MapMarker[];
  activeLayers: string[];
  highlightPfzIds: string[];
  route?: { direct: Array<[number, number]>; recommended: Array<[number, number]>; waypoints: RouteWaypoint[] };
  boundaryWarning?: { name: string; distanceNm: number; severity: string };
}

export interface EvidenceItem {
  fact: string;
  value: string;
  agent: AgentName;
  provenance: Provenance;
}

export interface AnswerPayload {
  text: string;
  language: Lang;
  mode: "llm" | "template";
  evidence: EvidenceItem[];
  confidence: number;
  band?: RiskBand;
}

// ---------------------------------------------------------------- stream protocol

export type StreamEvent =
  | { type: "meta"; runId: string; query: string; startedAt: string; llmAvailable: boolean; scenarioId?: string }
  | { type: "plan"; plan: QueryPlan; durationMs: number }
  | { type: "step_start"; agent: AgentName; task: string; index: number; total: number }
  | { type: "step_end"; result: AgentResult }
  | { type: "risk"; risk: RiskData }
  | { type: "map"; map: MapPayload }
  | { type: "answer"; answer: AnswerPayload }
  | { type: "notice"; level: "info" | "warn"; message: string }
  | { type: "done"; totalMs: number; degradedAgents: string[] }
  | { type: "error"; message: string };
