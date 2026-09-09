/**
 * OCEAN AGENT
 * Tool: Open-Meteo Marine API (marine-api.open-meteo.com) - free, keyless.
 * Returns sea state: significant wave height, wave direction/period, swell, and SST.
 * Like the weather agent it reports the worst hour in the window.
 */
import { fetchJson } from "@/lib/net";
import { cacheGet, cacheSet } from "@/lib/cache";
import { prov } from "@/lib/provenance";
import { windowIndices, maxOver, meanOver, atPeak, pick } from "@/lib/series";
import { getSnapshot } from "@/lib/snapshots";
import type { AgentOpts, ArchiveWindow, AgentResult, ToolCall, OceanData, TimeWindow, ResolvedLocation, Provenance } from "@/lib/types";

const HOURLY =
  "wave_height,wave_direction,wave_period,swell_wave_height,swell_wave_period,sea_surface_temperature";

export function marineUrl(lat: number, lon: number, archive?: ArchiveWindow): string {
  const base = `https://marine-api.open-meteo.com/v1/marine?latitude=${lat.toFixed(4)}` +
    `&longitude=${lon.toFixed(4)}&hourly=${HOURLY}&timezone=Asia%2FKolkata`;
  return archive ? `${base}&start_date=${archive.startDate}&end_date=${archive.endDate}` : `${base}&forecast_days=3`;
}

interface OMMarine {
  latitude: number; longitude: number;
  hourly_units: Record<string, string>;
  hourly: { time: string[]; [k: string]: unknown };
}

export async function runOceanAgent(
  location: ResolvedLocation,
  window: TimeWindow,
  opts: AgentOpts = {},
): Promise<AgentResult<OceanData>> {
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  const task = `Fetch sea state at ${location.name} for ${window.label}`;
  const toolCalls: ToolCall[] = [];
  const provenance: Provenance[] = [];

  const url = marineUrl(location.lat, location.lon, opts.archive);
  const key = `marine:${location.lat.toFixed(3)}:${location.lon.toFixed(3)}:${opts.archive?.startDate ?? "live"}`;

  let payload: OMMarine | null = null;
  let degraded = false;
  let penalty = 0;
  let error: string | undefined;

  const cached = cacheGet<OMMarine>(key);
  if (cached) {
    payload = cached.value;
    toolCalls.push({
      tool: "memory-cache", url, status: "cache", durationMs: 0,
      summary: `Cache hit, ${cached.ageSeconds}s old (10 min TTL)`,
      raw: { cachedAt: cached.storedAtISO, ageSeconds: cached.ageSeconds },
    });
    provenance.push(oceanProv(opts.archive, url, cached.storedAtISO, cached.ageSeconds));
  } else {
    const res = await fetchJson<OMMarine>(url, opts.timeoutMs ?? 6000);
    toolCalls.push({
      tool: opts.archive ? "http.get open-meteo/marine-archive" : "http.get open-meteo/marine", url,
      params: { latitude: location.lat, longitude: location.lon, hourly: HOURLY, timezone: "Asia/Kolkata" },
      status: res.status, durationMs: res.durationMs,
      summary: res.ok
        ? `200 OK, ${res.data?.hourly.time.length ?? 0} hourly steps`
        : `${res.status}: ${res.error}`,
      raw: res.ok ? trimMarine(res.data!) : { error: res.error },
    });
    if (res.ok && res.data && Array.isArray(res.data.hourly?.wave_height)) {
      payload = res.data;
      cacheSet(key, res.data);
      provenance.push(oceanProv(opts.archive, url));
    } else {
      error = res.error ?? "Marine model returned no wave data for this coordinate";
      const snap = getSnapshot(location, opts.scenarioId);
      if (snap?.marine) {
        payload = snap.marine as unknown as OMMarine;
        degraded = true;
        penalty = 0.2;
        toolCalls.push({
          tool: "snapshot.load", status: "fallback", durationMs: 0,
          summary: `Live call failed - loaded cached snapshot captured ${snap.capturedAt}`,
          raw: { snapshotId: snap.id, capturedAt: snap.capturedAt },
        });
        provenance.push(
          prov("Open-Meteo Marine API", "SNAPSHOT", {
            fetchedAt: snap.capturedAt, url,
            note: "Cached snapshot of a real prior response - the live call failed or timed out",
          }),
        );
      }
    }
  }

  if (!payload) {
    return {
      agent: "ocean", task, ok: false, degraded: true, data: null,
      error: error ?? "No marine data available",
      confidencePenalty: 0.4, provenance, toolCalls,
      durationMs: Date.now() - t0, startedAt,
    };
  }

  const h = payload.hourly;
  const idx = windowIndices(h.time, window.startIST, window.endIST);
  const waveMax = maxOver(h.wave_height, idx);

  if (waveMax === null) {
    return {
      agent: "ocean", task, ok: false, degraded: true, data: null,
      error: "Marine model has no wave solution at this coordinate (likely an inland or lake point)",
      confidencePenalty: 0.4, provenance, toolCalls,
      durationMs: Date.now() - t0, startedAt,
    };
  }

  const data: OceanData = {
    waveHeightM: waveMax,
    waveDirectionDeg: atPeak(h.wave_direction, h.wave_height, idx) ?? 0,
    wavePeriodS: atPeak(h.wave_period, h.wave_height, idx) ?? 0,
    swellHeightM: maxOver(h.swell_wave_height, idx) ?? 0,
    swellPeriodS: meanOver(h.swell_wave_period, idx) ?? 0,
    seaSurfaceTempC: meanOver(h.sea_surface_temperature, idx) ?? 0,
    windowLabel: window.label,
    series: idx.map((i) => ({
      t: h.time[i],
      wave: pick(h.wave_height, i) ?? 0,
      swell: pick(h.swell_wave_height, i) ?? 0,
    })),
  };

  return {
    agent: "ocean", task, ok: true, degraded, data,
    error, confidencePenalty: penalty, provenance, toolCalls,
    durationMs: Date.now() - t0, startedAt,
  };
}

function oceanProv(archive: ArchiveWindow | undefined, url: string, fetchedAt?: string, ageSeconds?: number) {
  if (archive) {
    return prov("Open-Meteo Marine Archive (wave reanalysis)", "ARCHIVE", {
      fetchedAt: `${archive.startDate}T00:00:00+05:30`,
      url,
      note: `Historical replay: ${archive.label}. NOT current conditions.`,
    });
  }
  return prov("Open-Meteo Marine API", "LIVE", { fetchedAt, ageSeconds, url });
}

function trimMarine(d: OMMarine) {
  const out: Record<string, unknown> = {
    latitude: d.latitude, longitude: d.longitude,
    hourly_units: d.hourly_units,
    _note: "first 6 of 72 hourly steps shown in trace; full series used for computation",
  };
  const hourly: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(d.hourly)) hourly[k] = Array.isArray(v) ? v.slice(0, 6) : v;
  out.hourly = hourly;
  return out;
}
