import type {
  CellTrace, ChatResponse, GeofenceResponse, RecallResponse,
  RiskFeatureCollection, RiskTimes, ScenarioStatus, ZoneFeatureCollection,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const fetchTimes = () => get<RiskTimes>("/api/risk/times");

export const fetchRisk = (bbox: [number, number, number, number], time?: string) =>
  get<RiskFeatureCollection>(
    `/api/risk?bbox=${bbox.join(",")}` + (time ? `&time=${encodeURIComponent(time)}` : "")
  );

export const fetchTrace = (cell: string, time?: string) =>
  get<CellTrace>(
    `/api/cells/${cell}/trace` + (time ? `?time=${encodeURIComponent(time)}` : "")
  );

export const fetchGeofence = (lat: number, lon: number, bufferNm = 2) =>
  get<GeofenceResponse>(`/api/geofence?lat=${lat}&lon=${lon}&buffer_nm=${bufferNm}`);

export const fetchZones = () => get<ZoneFeatureCollection>("/api/zones");

export async function sendChat(message: string, sessionId: string | null) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON */ }
    throw new Error(detail);
  }
  return (await res.json()) as ChatResponse;
}

export const fetchRecall = (threshold = 0.05) =>
  get<RecallResponse>(`/api/recall?threshold=${threshold}`);

export const fetchScenarioStatus = () =>
  get<ScenarioStatus>("/api/scenario/status");

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "POST" });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* non-JSON */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const activateScenario = (name: string) =>
  post<Record<string, unknown>>(`/api/scenario/${name}/activate`);
export const clearScenario = () =>
  post<Record<string, unknown>>("/api/scenario/clear");
