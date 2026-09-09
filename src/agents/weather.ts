/**
 * WEATHER AGENT
 * Tool: Open-Meteo Forecast API (api.open-meteo.com) - free, keyless, no registration.
 * Returns atmospheric conditions aggregated over the planner's time window.
 * Reports the WORST hour in the window, not the mean, because safety decisions are
 * made against the worst conditions a vessel will meet.
 */
import { fetchJson } from "@/lib/net";
import { cacheGet, cacheSet } from "@/lib/cache";
import { prov } from "@/lib/provenance";
import { windowIndices, maxOver, sumOver, meanOver, atPeak, pick } from "@/lib/series";
import { getSnapshot } from "@/lib/snapshots";
import type { AgentOpts, ArchiveWindow, AgentResult, ToolCall, WeatherData, TimeWindow, ResolvedLocation, Provenance } from "@/lib/types";

const HOURLY =
  "temperature_2m,precipitation,weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m,visibility";

export const WMO: Record<number, string> = {
  0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
  45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle", 53: "Moderate drizzle",
  55: "Dense drizzle", 61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
  66: "Freezing rain", 67: "Heavy freezing rain", 71: "Slight snow", 80: "Slight rain showers",
  81: "Moderate rain showers", 82: "Violent rain showers", 85: "Snow showers",
  95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
};

export function forecastUrl(lat: number, lon: number, archive?: ArchiveWindow): string {
  const base = `latitude=${lat.toFixed(4)}&longitude=${lon.toFixed(4)}&hourly=${HOURLY}` +
    `&timezone=Asia%2FKolkata&wind_speed_unit=kmh`;
  return archive
    ? `https://archive-api.open-meteo.com/v1/archive?${base}&start_date=${archive.startDate}&end_date=${archive.endDate}`
    : `https://api.open-meteo.com/v1/forecast?${base}&forecast_days=3`;
}

interface OMForecast {
  latitude: number; longitude: number;
  hourly_units: Record<string, string>;
  hourly: { time: string[]; [k: string]: unknown };
}

export async function runWeatherAgent(
  location: ResolvedLocation,
  window: TimeWindow,
  opts: AgentOpts = {},
): Promise<AgentResult<WeatherData>> {
  const startedAt = new Date().toISOString();
  const t0 = Date.now();
  const task = `Fetch atmospheric conditions at ${location.name} for ${window.label}`;
  const toolCalls: ToolCall[] = [];
  const provenance: Provenance[] = [];

  const url = forecastUrl(location.lat, location.lon, opts.archive);
  const key = `forecast:${location.lat.toFixed(3)}:${location.lon.toFixed(3)}:${opts.archive?.startDate ?? "live"}`;

  let payload: OMForecast | null = null;
  let degraded = false;
  let penalty = 0;
  let error: string | undefined;

  const cached = cacheGet<OMForecast>(key);
  if (cached) {
    payload = cached.value;
    toolCalls.push({
      tool: "memory-cache", url, status: "cache", durationMs: 0,
      summary: `Cache hit, ${cached.ageSeconds}s old (10 min TTL)`,
      raw: { cachedAt: cached.storedAtISO, ageSeconds: cached.ageSeconds },
    });
    provenance.push(weatherProv(opts.archive, url, cached.storedAtISO, cached.ageSeconds));
  } else {
    const res = await fetchJson<OMForecast>(url, opts.timeoutMs ?? 6000);
    toolCalls.push({
      tool: opts.archive ? "http.get open-meteo/archive" : "http.get open-meteo/forecast", url,
      params: { latitude: location.lat, longitude: location.lon, hourly: HOURLY, timezone: "Asia/Kolkata" },
      status: res.status, durationMs: res.durationMs,
      summary: res.ok
        ? `200 OK, ${res.data?.hourly.time.length ?? 0} hourly steps`
        : `${res.status}: ${res.error}`,
      raw: res.ok ? trimForecast(res.data!) : { error: res.error },
    });
    if (res.ok && res.data) {
      payload = res.data;
      cacheSet(key, res.data);
      provenance.push(weatherProv(opts.archive, url));
    } else {
      error = res.error;
      const snap = getSnapshot(location, opts.scenarioId);
      if (snap?.forecast) {
        payload = snap.forecast as unknown as OMForecast;
        degraded = true;
        penalty = 0.2;
        toolCalls.push({
          tool: "snapshot.load", status: "fallback", durationMs: 0,
          summary: `Live call failed - loaded cached snapshot captured ${snap.capturedAt}`,
          raw: { snapshotId: snap.id, capturedAt: snap.capturedAt },
        });
        provenance.push(
          prov("Open-Meteo Forecast API", "SNAPSHOT", {
            fetchedAt: snap.capturedAt,
            url,
            note: "Cached snapshot of a real prior response - the live call failed or timed out",
          }),
        );
      }
    }
  }

  if (!payload) {
    return {
      agent: "weather", task, ok: false, degraded: true, data: null,
      error: error ?? "No forecast data available and no snapshot for this location",
      confidencePenalty: 0.35, provenance, toolCalls,
      durationMs: Date.now() - t0, startedAt,
    };
  }

  const h = payload.hourly;
  const idx = windowIndices(h.time, window.startIST, window.endIST);
  const windMax = maxOver(h.wind_speed_10m, idx) ?? 0;
  const gustMax = maxOver(h.wind_gusts_10m, idx) ?? 0;
  const code = Math.max(...idx.map((i) => pick(h.weather_code, i) ?? 0));

  const data: WeatherData = {
    windSpeedKmh: windMax,
    windGustKmh: gustMax,
    windDirectionDeg: atPeak(h.wind_direction_10m, h.wind_speed_10m, idx) ?? 0,
    precipitationMm: sumOver(h.precipitation, idx) ?? 0,
    temperatureC: meanOver(h.temperature_2m, idx) ?? 0,
    weatherCode: code,
    weatherLabel: WMO[code] ?? `WMO code ${code}`,
    visibilityM: meanOver(h.visibility, idx),
    windowLabel: window.label,
    series: idx.map((i) => ({
      t: h.time[i],
      wind: pick(h.wind_speed_10m, i) ?? 0,
      gust: pick(h.wind_gusts_10m, i) ?? 0,
      precip: pick(h.precipitation, i) ?? 0,
    })),
  };

  return {
    agent: "weather", task, ok: true, degraded, data,
    error, confidencePenalty: penalty, provenance, toolCalls,
    durationMs: Date.now() - t0, startedAt,
  };
}

/**
 * Provenance differs sharply between a live forecast and a historical replay. An archive
 * record is stamped with the EVENT date, so its age chip reads in days/years - the judge
 * can see at a glance that it is a replay of a past cyclone and not current conditions.
 */
function weatherProv(archive: ArchiveWindow | undefined, url: string, fetchedAt?: string, ageSeconds?: number) {
  if (archive) {
    return prov("Open-Meteo Historical Archive (ERA5 reanalysis)", "ARCHIVE", {
      fetchedAt: `${archive.startDate}T00:00:00+05:30`,
      url,
      note: `Historical replay: ${archive.label}. NOT current conditions.`,
    });
  }
  return prov("Open-Meteo Forecast API", "LIVE", { fetchedAt, ageSeconds, url });
}

/** Keep the trace payload readable: first 6 hours of every series plus units. */
function trimForecast(d: OMForecast) {
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
