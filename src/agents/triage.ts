/**
 * TRIAGE AGENT — vessel recall prioritisation during a cyclone.
 *
 * The problem, stated the way a coastal officer has it: a cyclone track is closing on the
 * coast, some thousands of boats are at sea, and the recall order has to be decided in
 * minutes. Which boat gets the call first?
 *
 * The answer is not "the closest to the storm". It is the one with the smallest MARGIN:
 *
 *     margin = time_until_damaging_wind_reaches_the_vessel
 *            − time_the_vessel_needs_to_reach_a_safe_harbour
 *
 * A boat 200 nm from the storm with a dead-slow engine and no harbour upwind is in more
 * trouble than a fast boat 80 nm away sitting next to a harbour. Margin captures that;
 * distance-to-storm does not.
 *
 * Two refinements that matter and are easy to miss:
 *
 *  1. The NEAREST harbour is often the wrong harbour, because the shortest run may be
 *     straight into the storm's path. Every harbour is evaluated, and only those reachable
 *     before damaging wind arrives are eligible. The recommended harbour is the safest
 *     reachable one, not the closest one.
 *
 *  2. Vessel speed is not cruise speed. A small boat punching into a rising head sea makes
 *     far less than its rated speed, so effective speed is derated from the sea state.
 *
 * DETERMINISTIC. This is a solver, not a model. No LLM touches any number here. The LLM's only
 * job downstream is to read the list out in the fisherman's language.
 */
import { distanceKm, bearingDeg, compass, KM_PER_NM } from "@/lib/geo";
import { HARBOURS } from "@/lib/layers";
import { hazardArrival, hazardRadiusNm, cycloneProvenance, type CycloneField } from "@/lib/cyclone";
import { prov } from "@/lib/provenance";
import type { AgentResult, Provenance, ToolCall } from "@/lib/types";
import type { Vessel } from "@/lib/fleet";

export const SOLVER_VERSION = "orca-triage-solver-v1";

/**
 * Sea-state derating of vessel speed.
 *
 * A small open or half-decked boat loses way rapidly as head seas build: slamming forces the
 * skipper to throttle back. These factors are the operational rule of thumb used for coastal
 * craft, documented in RISK-RULES.md. Applied multiplicatively to rated cruise speed.
 */
export const SEA_STATE_DERATE = [
  { minWaveM: 4.0, factor: 0.35, label: "very rough - barely able to make way" },
  { minWaveM: 3.0, factor: 0.50, label: "rough - heavy slamming" },
  { minWaveM: 2.0, factor: 0.65, label: "moderate-rough - throttled back" },
  { minWaveM: 1.5, factor: 0.80, label: "moderate" },
  { minWaveM: 0.0, factor: 1.00, label: "slight" },
] as const;

export function effectiveSpeedKn(ratedKn: number, waveHeightM: number): { kn: number; factor: number; label: string } {
  const band = SEA_STATE_DERATE.find((b) => waveHeightM >= b.minWaveM) ?? SEA_STATE_DERATE[SEA_STATE_DERATE.length - 1];
  return { kn: +(ratedKn * band.factor).toFixed(2), factor: band.factor, label: band.label };
}

export type TriageBand = "CRITICAL" | "URGENT" | "WATCH" | "CLEAR";

/**
 * Margin thresholds, in hours. A margin below zero means the vessel cannot physically reach
 * any harbour before damaging wind arrives, which is the case a rescue service must know about
 * first. Documented in RISK-RULES.md.
 */
export const TRIAGE_THRESHOLDS = {
  critical: 2,
  urgent: 6,
  watch: 12,
} as const;

export function triageBand(marginH: number | null, alreadyExposed: boolean): TriageBand {
  if (alreadyExposed) return "CRITICAL";
  if (marginH === null) return "CLEAR";
  if (marginH < TRIAGE_THRESHOLDS.critical) return "CRITICAL";
  if (marginH < TRIAGE_THRESHOLDS.urgent) return "URGENT";
  if (marginH < TRIAGE_THRESHOLDS.watch) return "WATCH";
  return "CLEAR";
}

export interface HarbourOption {
  id: string;
  name: string;
  distanceNm: number;
  bearingDeg: number;
  compass: string;
  runTimeH: number;
  /** Reachable before damaging wind arrives at the HARBOUR, with the safety factor applied. */
  reachable: boolean;
}

export interface TriageResult {
  vessel: Vessel;
  /** Hours until damaging wind reaches the vessel's current position. */
  timeToHazardH: number | null;
  hazardArrivalIso: string | null;
  alreadyExposed: boolean;
  closestApproachNm: number;
  /** Hours to reach the RECOMMENDED harbour at derated speed. */
  timeToHarbourH: number | null;
  recommendedHarbour: HarbourOption | null;
  nearestHarbour: HarbourOption;
  /** True when the nearest harbour was rejected because the run heads into the storm. */
  divertedFromNearest: boolean;
  marginH: number | null;
  band: TriageBand;
  /** 0 exposed, 1 unreachable, 2 reachable, 3 unaffected. Primary sort key. */
  priorityGroup: 0 | 1 | 2 | 3;
  /** Secondary sort key, meaning depends on the group. Ascending is more urgent. */
  priorityValue: number;
  effectiveSpeedKn: number;
  seaStateLabel: string;
  reason: string;
}

export interface TriageInput {
  fleet: Vessel[];
  cyclone: CycloneField;
  /** Naive-safe assumption for sea state across the operating area, metres. */
  waveHeightM: number;
  /** Decision time. Defaults to the cyclone's own current-position timestamp for a replay. */
  originIso: string;
  /** Require this much slack on top of the run time before calling a harbour reachable. */
  safetyFactorH?: number;
}

export interface TriageSummary {
  originIso: string;
  cycloneName: string;
  alertLevel: string;
  maxWindKmh: number;
  hazardRadiusNm: number;
  intensityLabel: string;
  isReplay: boolean;
  fleetSize: number;
  counts: Record<TriageBand, number>;
  /** Vessels that cannot reach any harbour in time. */
  unreachable: number;
  /** Vessels whose recommended harbour is not their nearest. */
  diverted: number;
  solver: string;
  safetyFactorH: number;
}

/**
 * Core solver. Pure function of fleet + hazard field + sea state.
 */
export function solveTriage(input: TriageInput): { results: TriageResult[]; summary: TriageSummary } {
  const { fleet, cyclone, waveHeightM, originIso } = input;
  const safetyFactorH = input.safetyFactorH ?? 1;
  const { radiusNm, label: intensityLabel } = hazardRadiusNm(cyclone.maxWindKmh);

  const results: TriageResult[] = fleet.map((v) => {
    const arrival = hazardArrival({ lon: v.lon, lat: v.lat }, cyclone, originIso);
    const eff = effectiveSpeedKn(v.ratedSpeedKn, waveHeightM);

    // Evaluate EVERY harbour, not just the nearest.
    const options: HarbourOption[] = HARBOURS.map((h) => {
      const nm = distanceKm([v.lon, v.lat], [h.lon, h.lat]) / KM_PER_NM;
      const runTimeH = eff.kn > 0 ? nm / eff.kn : Infinity;
      // Does damaging wind reach the HARBOUR before the vessel does?
      const harbourArrival = hazardArrival({ lon: h.lon, lat: h.lat }, cyclone, originIso);
      const harbourSafeUntilH = harbourArrival.alreadyExposed
        ? 0
        : harbourArrival.hoursUntil === null
          ? Infinity
          : harbourArrival.hoursUntil;
      const vesselHazardH = arrival.alreadyExposed ? 0 : arrival.hoursUntil ?? Infinity;
      // Reachable if the run fits inside BOTH the vessel's own exposure window and the
      // harbour's, with the safety factor applied.
      const reachable =
        Number.isFinite(runTimeH) &&
        runTimeH + safetyFactorH <= Math.min(vesselHazardH, harbourSafeUntilH);
      const bd = bearingDeg([v.lon, v.lat], [h.lon, h.lat]);
      return {
        id: h.id, name: h.name,
        distanceNm: +nm.toFixed(1),
        bearingDeg: +bd.toFixed(0), compass: compass(bd),
        runTimeH: Number.isFinite(runTimeH) ? +runTimeH.toFixed(2) : Infinity,
        reachable,
      };
    }).sort((a, b) => a.distanceNm - b.distanceNm);

    const nearestHarbour = options[0];
    const reachableOptions = options.filter((o) => o.reachable);
    const recommendedHarbour = reachableOptions.length ? reachableOptions[0] : null;
    const divertedFromNearest = Boolean(recommendedHarbour && recommendedHarbour.id !== nearestHarbour.id);

    const timeToHarbourH = recommendedHarbour ? recommendedHarbour.runTimeH : null;
    const timeToHazardH = arrival.alreadyExposed ? 0 : arrival.hoursUntil;

    // Margin is only defined when both legs are defined. No sentinel values: an unreachable
    // vessel has NO margin, and is ranked by how soon the hazard hits it instead.
    const marginH: number | null =
      timeToHazardH === null || timeToHarbourH === null
        ? null
        : +(timeToHazardH - timeToHarbourH).toFixed(2);

    const band = triageBand(
      timeToHarbourH === null && timeToHazardH !== null ? -1 : marginH,
      arrival.alreadyExposed,
    );

    /**
     * Priority grouping. Ordering inside each group is what makes the list actionable:
     *   0  already inside damaging wind      -> immediate distress response
     *   1  cannot reach any harbour in time  -> rank by SOONEST hazard arrival (rescue window)
     *   2  can reach a harbour               -> rank by SMALLEST margin (tightest recall)
     *   3  storm never reaches them          -> rank by closest approach
     */
    const priorityGroup = arrival.alreadyExposed
      ? 0
      : timeToHazardH === null
        ? 3
        : timeToHarbourH === null
          ? 1
          : 2;
    const priorityValue =
      priorityGroup === 0 ? 0
      : priorityGroup === 1 ? (timeToHazardH ?? 0)
      : priorityGroup === 2 ? (marginH ?? 0)
      : arrival.closestApproachNm;

    let reason: string;
    if (arrival.alreadyExposed) {
      reason = `Already inside the ${radiusNm} nm damaging-wind radius. Immediate distress response, not a recall.`;
    } else if (timeToHazardH === null) {
      reason = `Storm does not reach this position on the current forecast track. Closest approach ${arrival.closestApproachNm} nm.`;
    } else if (timeToHarbourH === null) {
      reason = `Cannot reach ANY harbour before damaging wind arrives in ${timeToHazardH} h at ${eff.kn} kn effective speed. Escalate to rescue.`;
    } else if (divertedFromNearest) {
      reason = `Nearest harbour ${nearestHarbour.name} (${nearestHarbour.distanceNm} nm) is not usable - the run or the harbour itself is inside the storm window. Divert to ${recommendedHarbour!.name}, ${recommendedHarbour!.distanceNm} nm ${recommendedHarbour!.compass}.`;
    } else {
      reason = `Run ${recommendedHarbour!.distanceNm} nm ${recommendedHarbour!.compass} to ${recommendedHarbour!.name}, ${timeToHarbourH} h at ${eff.kn} kn. Damaging wind in ${timeToHazardH} h. Margin ${marginH} h.`;
    }

    return {
      vessel: v,
      timeToHazardH,
      hazardArrivalIso: arrival.arrivalIso,
      alreadyExposed: arrival.alreadyExposed,
      closestApproachNm: arrival.closestApproachNm,
      timeToHarbourH,
      recommendedHarbour,
      nearestHarbour,
      divertedFromNearest,
      marginH,
      band,
      effectiveSpeedKn: eff.kn,
      seaStateLabel: eff.label,
      priorityGroup,
      priorityValue: +priorityValue.toFixed(2),
      reason,
    };
  });

  // Group first, then the group's own urgency measure. No sentinels, no ties broken by luck.
  results.sort((a, b) =>
    a.priorityGroup !== b.priorityGroup
      ? a.priorityGroup - b.priorityGroup
      : a.priorityValue - b.priorityValue,
  );

  const counts: Record<TriageBand, number> = { CRITICAL: 0, URGENT: 0, WATCH: 0, CLEAR: 0 };
  for (const r of results) counts[r.band]++;

  return {
    results,
    summary: {
      originIso,
      cycloneName: cyclone.eventname,
      alertLevel: cyclone.alertlevel,
      maxWindKmh: cyclone.maxWindKmh,
      hazardRadiusNm: radiusNm,
      intensityLabel,
      isReplay: cyclone.isReplay,
      fleetSize: fleet.length,
      counts,
      unreachable: results.filter((r) => r.timeToHarbourH === null && r.timeToHazardH !== null).length,
      diverted: results.filter((r) => r.divertedFromNearest).length,
      solver: SOLVER_VERSION,
      safetyFactorH,
    },
  };
}

/** Agent wrapper so triage appears in the reasoning trace like every other agent. */
export function runTriageAgent(input: TriageInput): AgentResult<{ results: TriageResult[]; summary: TriageSummary }> {
  const t0 = Date.now();
  const startedAt = new Date().toISOString();
  const data = solveTriage(input);

  const provenance: Provenance[] = [
    cycloneProvenance(input.cyclone),
    prov(`ORCA vessel recall triage solver ${SOLVER_VERSION}`, "DERIVED", {
      note: "Deterministic optimisation over the fleet. margin = time-to-hazard minus time-to-harbour at sea-state-derated speed, evaluating every harbour rather than only the nearest. No language model involved.",
    }),
    prov("ORCA harbour gazetteer", "CACHED", { note: `${HARBOURS.length} harbours evaluated per vessel.` }),
  ];

  const toolCalls: ToolCall[] = [
    {
      tool: "triage.solve",
      status: "ok",
      durationMs: Date.now() - t0,
      summary:
        `${data.summary.fleetSize} vessels x ${HARBOURS.length} harbours -> ` +
        `${data.summary.counts.CRITICAL} CRITICAL, ${data.summary.counts.URGENT} URGENT, ` +
        `${data.summary.counts.WATCH} WATCH, ${data.summary.counts.CLEAR} clear; ` +
        `${data.summary.unreachable} cannot reach any harbour, ${data.summary.diverted} diverted from nearest`,
      raw: {
        solver: SOLVER_VERSION,
        summary: data.summary,
        thresholdsH: TRIAGE_THRESHOLDS,
        seaStateDerate: SEA_STATE_DERATE,
        top5: data.results.slice(0, 5).map((r) => ({
          vessel: r.vessel.id,
          marginH: r.marginH,
          timeToHazardH: r.timeToHazardH,
          timeToHarbourH: r.timeToHarbourH,
          band: r.band,
          recommended: r.recommendedHarbour?.name ?? null,
          reason: r.reason,
        })),
      },
    },
  ];

  return {
    agent: "triage",
    task: `Prioritise recall for ${input.fleet.length} vessels against ${input.cyclone.eventname}`,
    ok: true,
    degraded: false,
    data,
    confidencePenalty: 0,
    provenance,
    toolCalls,
    durationMs: Date.now() - t0,
    startedAt,
  };
}
