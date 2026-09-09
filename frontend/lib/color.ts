// Colour scale for hazard_prob.
//
// THE DOMAIN IS FIXED AT 0 -> 1. NOT stretched to the observed range.
//
// This is not a styling preference. Stretching the scale to whatever the data
// happens to span would paint the roughest cell deep red at a 23% exceedance
// probability, and a viewer would read the map as "severe" when the sea is
// calm. That is the same dishonesty as a dimensionless weighted-sum risk
// score, and it would undermine the entire argument for computing real
// probabilities in the first place.
//
// A calm sea must look calm. The legend states the observed maximum so the
// flatness reads as information, not as a broken map.

export const HAZARD_STOPS: Array<[number, string]> = [
  [0.0, "#e8f0f7"],
  [0.1, "#a8d5c8"],
  [0.25, "#f2dd8a"],
  [0.5, "#f0a05a"],
  [0.75, "#d9534f"],
  [1.0, "#6e1414"],
];

/** Colour for a cell with no data at all. Deliberately not the 0.0 colour:
 *  "we don't know" and "it's calm" are opposite claims. */
export const NO_DATA_COLOR = "#9aa0a6";

export const SIMULATED_OUTLINE = "#c026d3";

export function hazardColor(p: number | null): string {
  if (p === null || p === undefined) return NO_DATA_COLOR;
  const v = Math.min(1, Math.max(0, p));
  for (let i = 1; i < HAZARD_STOPS.length; i++) {
    const [x1, c1] = HAZARD_STOPS[i - 1];
    const [x2, c2] = HAZARD_STOPS[i];
    if (v <= x2) return mix(c1, c2, (v - x1) / (x2 - x1));
  }
  return HAZARD_STOPS[HAZARD_STOPS.length - 1][1];
}

function mix(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const p = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `#${p.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

export const fmtPct = (p: number | null) =>
  p === null || p === undefined ? "no data" : `${(p * 100).toFixed(1)}%`;

export function fmtAge(seconds: number | null, lowerBound: boolean): string {
  if (seconds === null) return "unknown";
  const m = seconds / 60;
  const s = m < 90 ? `${m.toFixed(0)} min` : `${(m / 60).toFixed(1)} h`;
  // "at least" because a fetch-proxy issued_time is when WE retrieved the
  // value, not when the model produced it -- the true age is larger.
  return lowerBound ? `at least ${s}` : s;
}
