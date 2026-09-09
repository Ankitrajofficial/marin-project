import type { CellTrace, RiskFeatureCollection, RiskTimes } from "./types";

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
