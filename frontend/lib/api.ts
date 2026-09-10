import type {
  CellTrace, ChatResponse, GeofenceResponse, RecallResponse,
  RiskFeatureCollection, RiskTimes, ScenarioStatus, ZoneFeatureCollection,
} from "./types";

// Empty string = same origin: every request below becomes a relative URL, and
// next.config.mjs rewrites /api/* to the FastAPI process. One port, no CORS.
//
// Set NEXT_PUBLIC_API_BASE only to point the browser straight at a backend on
// another origin (which then needs that origin in the API's CORS allow-list).
const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

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

// ---------------------------------------------------------------------------
// Streaming chat. EventSource cannot POST, so the SSE frames are parsed off a
// fetch body stream by hand -- which is a handful of lines and avoids moving
// the request into a query string.
export interface StreamHandlers {
  onOpen?: (sessionId: string) => void;
  onPhase?: (label: string, phase: string) => void;
  onPlan?: (intent: string, labels: string[]) => void;
  onToken?: (text: string) => void;
  onDone?: (payload: ChatResponse & { trace_id?: number }) => void;
  onError?: (message: string) => void;
}

export async function streamChat(
  message: string, sessionId: string | null, h: StreamHandlers,
  signal?: AbortSignal,
) {
  const res = await fetch(`${BASE}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
    signal,
  });
  if (!res.ok || !res.body) {
    h.onError?.(`HTTP ${res.status} ${res.statusText}`);
    return;
  }

  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });

    // SSE frames are separated by a blank line. Anything after the last
    // separator is a partial frame and stays in the buffer.
    const frames = buf.split("\n\n");
    buf = frames.pop() ?? "";

    for (const frame of frames) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (!dataLines.length) continue;
      let d: Record<string, unknown>;
      try { d = JSON.parse(dataLines.join("\n")); } catch { continue; }

      switch (event) {
        case "open": h.onOpen?.(d.session_id as string); break;
        case "plan":
          h.onPlan?.(d.intent as string,
            ((d.calls ?? []) as Array<{ label: string }>).map((c) => c.label));
          break;
        case "phase": h.onPhase?.(d.label as string, d.phase as string); break;
        case "token": h.onToken?.(d.text as string); break;
        case "done": h.onDone?.(d as unknown as ChatResponse); break;
        case "error": h.onError?.(d.message as string); break;
      }
    }
  }
}
