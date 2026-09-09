/**
 * RISK AGENT - DETERMINISTIC RULE ENGINE. NO LLM. EVER.
 *
 * This is the single most important design decision in ORCA. Hazard classification is a
 * pure function of measured values and published thresholds. A language model plans the
 * query and explains the result; it never decides whether it is safe to go to sea.
 *
 * That property is what makes this auditable: given the same inputs this function returns
 * the same output, every time, and every triggered rule carries its measured value, its
 * threshold and its source. See RISK-RULES.md for the full threshold table and citations.
 *
 * Scoring: each rule contributes points to a 0-100 scale. 0 = benign, 100 = do not sail.
 * Bands: 0-33 SAFE, 34-66 CAUTION, 67-100 UNSAFE.
 */
import type {
  AgentResult, GeospatialData, OceanData, RiskBand, RiskData, TriggeredRule, WeatherData,
} from "@/lib/types";
import { prov } from "@/lib/provenance";

export const ENGINE_VERSION = "orca-deterministic-rules-v1";
export const VESSEL_CLASS = "Small mechanised / motorised fishing vessel (< 20 m LOA)";

export const BANDS = { SAFE: 33, CAUTION: 66 } as const;

/**
 * Threshold table. Every entry is mirrored in RISK-RULES.md with its justification.
 * `points` is the contribution when the rule fires.
 */
export const THRESHOLDS = {
  waveCaution: { v: 1.5, points: 18, source: "INCOIS high-wave advisory band for small craft (1.5-2.5 m)" },
  waveDanger: { v: 2.5, points: 34, source: "INCOIS High Wave Alert threshold for the Indian coast" },
  swellCaution: { v: 2.0, points: 10, source: "Long-period swell hazardous to small craft at harbour mouths" },
  swellDanger: { v: 3.0, points: 20, source: "INCOIS surf/swell surge alert band" },
  windCaution: { v: 25, points: 12, source: "Beaufort 4-5 (25-38 km/h): choppy sea, small craft advisory" },
  windDanger: { v: 45, points: 28, source: "IMD fishermen warning: do not venture at >= 45 km/h" },
  gustCaution: { v: 40, points: 8, source: "Gust factor > 1.5x sustained indicates squall activity" },
  gustDanger: { v: 55, points: 22, source: "IMD squall threshold (>= 55 km/h) - capsize risk for small craft" },
  precipCaution: { v: 10, points: 6, source: "IMD 'moderate rain' band; visibility degradation begins" },
  precipDanger: { v: 35, points: 14, source: "IMD 'heavy rain' band (>= 35 mm accumulated in window)" },
  steepSeaPeriod: { v: 6, points: 10, source: "Wave period < 6 s with Hs > 2 m = short steep sea, high capsize risk" },
  thunderstorm: { v: 95, points: 20, source: "WMO code >= 95: thunderstorm / lightning activity" },
  visibilityDanger: { v: 2000, points: 12, source: "Visibility < 2 km: collision risk, IMD dense fog criterion" },
  imblCritical: { v: 2, points: 40, source: "Within 2 nm of the IMBL: imminent crossing / detention risk" },
  imblWarning: { v: 5, points: 22, source: "Within 5 nm of the IMBL: MHA/Coast Guard advisory buffer" },
  imblAdvisory: { v: 10, points: 8, source: "Within 10 nm of the IMBL: awareness buffer" },
  mpaInside: { v: 1, points: 30, source: "Inside a gazetted no-fishing MPA core: statutory prohibition" },
} as const;

export interface RiskInputs {
  weather: WeatherData | null;
  ocean: OceanData | null;
  geo: GeospatialData | null;
  /** Sum of confidencePenalty from every upstream agent that degraded or failed. */
  upstreamPenalty: number;
  missing: string[];
}

export function computeRisk(input: RiskInputs): RiskData {
  const rules: TriggeredRule[] = [];
  const cleared: RiskData["cleared"] = [];
  const { weather: w, ocean: o, geo: g } = input;

  const fire = (
    id: string, label: string, measured: number, unit: string,
    t: { v: number; points: number; source: string }, severity: TriggeredRule["severity"],
    comparator: TriggeredRule["comparator"] = ">=",
    labelVars?: Record<string, string>,
  ) => rules.push({ id, label, labelVars, measured, unit, threshold: t.v, comparator, severity, points: t.points, source: t.source });

  const pass = (id: string, label: string, measured: number, unit: string, threshold: number) =>
    cleared.push({ id, label, measured, unit, threshold });

  // ---- sea state -------------------------------------------------------------
  if (o) {
    if (o.waveHeightM >= THRESHOLDS.waveDanger.v) {
      fire("wave-danger", "Significant wave height exceeds the high-wave alert threshold", o.waveHeightM, "m", THRESHOLDS.waveDanger, "danger");
    } else if (o.waveHeightM >= THRESHOLDS.waveCaution.v) {
      fire("wave-caution", "Significant wave height in the small-craft advisory band", o.waveHeightM, "m", THRESHOLDS.waveCaution, "caution");
    } else {
      pass("wave", "Significant wave height", o.waveHeightM, "m", THRESHOLDS.waveCaution.v);
    }

    if (o.swellHeightM >= THRESHOLDS.swellDanger.v) {
      fire("swell-danger", "Swell height in the surge alert band", o.swellHeightM, "m", THRESHOLDS.swellDanger, "danger");
    } else if (o.swellHeightM >= THRESHOLDS.swellCaution.v) {
      fire("swell-caution", "Swell hazardous at harbour mouths", o.swellHeightM, "m", THRESHOLDS.swellCaution, "caution");
    } else {
      pass("swell", "Swell height", o.swellHeightM, "m", THRESHOLDS.swellCaution.v);
    }

    if (o.wavePeriodS > 0 && o.wavePeriodS < THRESHOLDS.steepSeaPeriod.v && o.waveHeightM > 2) {
      fire("steep-sea", "Short steep sea: wave period below the safe threshold with high waves", o.wavePeriodS, "s", THRESHOLDS.steepSeaPeriod, "danger", "<");
    }
  }

  // ---- wind and weather ------------------------------------------------------
  if (w) {
    if (w.windSpeedKmh >= THRESHOLDS.windDanger.v) {
      fire("wind-danger", "Sustained wind at or above the IMD fishermen warning threshold", w.windSpeedKmh, "km/h", THRESHOLDS.windDanger, "danger");
    } else if (w.windSpeedKmh >= THRESHOLDS.windCaution.v) {
      fire("wind-caution", "Sustained wind in the small-craft advisory band", w.windSpeedKmh, "km/h", THRESHOLDS.windCaution, "caution");
    } else {
      pass("wind", "Sustained wind speed", w.windSpeedKmh, "km/h", THRESHOLDS.windCaution.v);
    }

    if (w.windGustKmh >= THRESHOLDS.gustDanger.v) {
      fire("gust-danger", "Gusts at squall strength", w.windGustKmh, "km/h", THRESHOLDS.gustDanger, "danger");
    } else if (w.windGustKmh >= THRESHOLDS.gustCaution.v) {
      fire("gust-caution", "Gusts indicate squall activity", w.windGustKmh, "km/h", THRESHOLDS.gustCaution, "caution");
    } else {
      pass("gust", "Wind gusts", w.windGustKmh, "km/h", THRESHOLDS.gustCaution.v);
    }

    if (w.precipitationMm >= THRESHOLDS.precipDanger.v) {
      fire("precip-danger", "Heavy rainfall accumulated across the window", w.precipitationMm, "mm", THRESHOLDS.precipDanger, "danger");
    } else if (w.precipitationMm >= THRESHOLDS.precipCaution.v) {
      fire("precip-caution", "Moderate rainfall degrading visibility", w.precipitationMm, "mm", THRESHOLDS.precipCaution, "caution");
    } else {
      pass("precip", "Rainfall in window", w.precipitationMm, "mm", THRESHOLDS.precipCaution.v);
    }

    if (w.weatherCode >= THRESHOLDS.thunderstorm.v) {
      fire("thunderstorm", "Thunderstorm / lightning activity forecast", w.weatherCode, "WMO code", THRESHOLDS.thunderstorm, "danger");
    }

    if (w.visibilityM !== null && w.visibilityM < THRESHOLDS.visibilityDanger.v) {
      fire("visibility", "Visibility below the safe navigation threshold", w.visibilityM, "m", THRESHOLDS.visibilityDanger, "danger", "<");
    }
  }

  // ---- restricted waters -----------------------------------------------------
  if (g?.boundary) {
    const nm = g.boundary.distanceNm;
    if (nm <= THRESHOLDS.imblCritical.v) {
      fire("imbl-critical", `Critically close to ${g.boundary.name}`, +nm.toFixed(2), "nm", THRESHOLDS.imblCritical, "danger", "<=", { name: g.boundary.name });
    } else if (nm <= THRESHOLDS.imblWarning.v) {
      fire("imbl-warning", `Inside the advisory buffer of ${g.boundary.name}`, +nm.toFixed(2), "nm", THRESHOLDS.imblWarning, "danger", "<=", { name: g.boundary.name });
    } else if (nm <= THRESHOLDS.imblAdvisory.v) {
      fire("imbl-advisory", `Approaching ${g.boundary.name}`, +nm.toFixed(2), "nm", THRESHOLDS.imblAdvisory, "caution", "<=", { name: g.boundary.name });
    } else {
      pass("imbl", "Distance to nearest maritime boundary", +nm.toFixed(1), "nm", THRESHOLDS.imblAdvisory.v);
    }
  }

  if (g?.mpa?.inside) {
    fire("mpa-inside", `Inside ${g.mpa.name} (${g.mpa.restriction.replace(/_/g, " ")})`, 1, "boolean", THRESHOLDS.mpaInside, "danger", ">=", { name: g.mpa.name, restriction: g.mpa.restriction.replace(/_/g, " ") });
  }

  // ---- score -----------------------------------------------------------------
  const raw = rules.reduce((sum, r) => sum + r.points, 0);
  const score = Math.max(0, Math.min(100, raw));

  /**
   * Sea state and wind are both REQUIRED to clear a vessel to sail. If either agent failed,
   * the engine refuses to produce a reassuring band: a score of 0 then means "no hazard was
   * detected in the data we have", which is not the same as "it is safe". Any triggered rule
   * still escalates normally - missing data can never downgrade a hazard we did observe.
   */
  const haveCore = Boolean(o) && Boolean(w);
  const band: RiskBand = !haveCore && score <= BANDS.CAUTION
    ? "UNKNOWN"
    : score > BANDS.CAUTION ? "UNSAFE" : score > BANDS.SAFE ? "CAUTION" : "SAFE";

  // Confidence is separate from risk: it says how much of the picture we actually have.
  const expected = 3; // weather, ocean, geospatial
  const present = [w, o, g].filter(Boolean).length;
  const dataCompleteness = +(present / expected).toFixed(2);
  const confidence = +Math.max(0.1, Math.min(1, dataCompleteness - input.upstreamPenalty)).toFixed(2);

  return {
    score, band, rules, cleared, confidence, dataCompleteness,
    missing: input.missing, engine: ENGINE_VERSION, vesselClass: VESSEL_CLASS,
  };
}

/** Agent wrapper so the risk engine appears in the trace like every other agent. */
export function runRiskAgent(input: RiskInputs): AgentResult<RiskData> {
  const t0 = Date.now();
  const startedAt = new Date().toISOString();
  const data = computeRisk(input);
  return {
    agent: "risk",
    task: `Evaluate ${Object.keys(THRESHOLDS).length} hazard thresholds against measured conditions (deterministic, no LLM)`,
    ok: true,
    degraded: data.dataCompleteness < 1,
    data,
    confidencePenalty: 0,
    provenance: [
      prov("ORCA deterministic rule engine " + ENGINE_VERSION, "DERIVED", {
        note: "Pure function of measured inputs and published thresholds. No language model involved. Thresholds documented in RISK-RULES.md.",
      }),
    ],
    toolCalls: [
      {
        tool: "rules.evaluate",
        status: "ok",
        durationMs: Date.now() - t0,
        summary: `${data.rules.length} rule(s) triggered, ${data.cleared.length} cleared -> score ${data.score}/100 (${data.band})`,
        raw: { engine: ENGINE_VERSION, triggered: data.rules, cleared: data.cleared, bands: BANDS },
      },
    ],
    durationMs: Date.now() - t0,
    startedAt,
  };
}
