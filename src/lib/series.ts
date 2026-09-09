/** Shared hourly-series helpers used by the weather and ocean agents. */

export interface HourlyBlock {
  time: string[];
  [k: string]: unknown;
}

/** Indices of the hourly array falling inside [startIST, endIST] (naive IST strings). */
export function windowIndices(times: string[], startIST: string, endIST: string): number[] {
  const idx: number[] = [];
  for (let i = 0; i < times.length; i++) {
    const t = times[i];
    if (t >= startIST && t <= endIST) idx.push(i);
  }
  // Never return empty: if the window is outside the forecast horizon, use the first 6 hours
  // available and let the caller flag the degradation.
  return idx.length ? idx : times.slice(0, 6).map((_, i) => i);
}

export function pick(arr: unknown, i: number): number | null {
  if (!Array.isArray(arr)) return null;
  const v = arr[i];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function maxOver(arr: unknown, idx: number[]): number | null {
  const vals = idx.map((i) => pick(arr, i)).filter((v): v is number => v !== null);
  return vals.length ? Math.max(...vals) : null;
}

export function meanOver(arr: unknown, idx: number[]): number | null {
  const vals = idx.map((i) => pick(arr, i)).filter((v): v is number => v !== null);
  return vals.length ? +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2) : null;
}

export function sumOver(arr: unknown, idx: number[]): number | null {
  const vals = idx.map((i) => pick(arr, i)).filter((v): v is number => v !== null);
  return vals.length ? +vals.reduce((a, b) => a + b, 0).toFixed(2) : null;
}

/** Value at the hour with the highest value of `driver` - i.e. conditions at the worst moment. */
export function atPeak(arr: unknown, driver: unknown, idx: number[]): number | null {
  let bestI = -1, bestV = -Infinity;
  for (const i of idx) {
    const v = pick(driver, i);
    if (v !== null && v > bestV) { bestV = v; bestI = i; }
  }
  return bestI >= 0 ? pick(arr, bestI) : null;
}
