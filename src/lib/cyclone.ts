/**
 * Cyclone hazard field.
 *
 * Parses a GDACS `getgeometry` response into a typed, timestamped track plus a forecast cone,
 * and answers the one question the triage solver needs:
 *
 *     "when does damaging wind reach this position?"
 *
 * AUTHORITY. GDACS aggregates NOAA/NHC and JTWC advisories. It is NOT IMD. For the North
 * Indian Ocean the official authority is RSMC New Delhi (IMD), which issues a Tropical Weather
 * Outlook daily at 0600 UTC plus an extra bulletin at 1700 UTC when a depression is forming.
 * GDACS output is the machine-readable feed and must never be labelled an IMD alert.
 * See DECISIONS.md.
 */
import { distanceKm, KM_PER_NM } from "./geo";
import type { Provenance } from "./types";
import { prov } from "./provenance";
import danaFixture from "@data/cyclones/dana-2024.json";

export interface TrackPoint {
  /** ISO instant, UTC. */
  at: string;
  label: string;
  lon: number;
  lat: number;
}

export interface CycloneField {
  eventid: number;
  episodeid: number;
  eventname: string;
  alertlevel: string;
  country: string;
  /** Max sustained wind, km/h, from GDACS severitydata. */
  maxWindKmh: number;
  severitytext: string;
  currentPosition: { lon: number; lat: number };
  track: TrackPoint[];
  cone: { ring: number[][] } | null;
  /** GDACS upstream advisory source, e.g. "JTWC". */
  upstreamSource: string;
  kind: "LIVE" | "ARCHIVE";
  isReplay: boolean;
  note: string;
}

/**
 * Damaging-wind radius as a function of max sustained wind.
 *
 * Small fishing vessels are lost well outside the eye. These radii approximate the gale-force
 * (>= 60 km/h) footprint of a North Indian Ocean system by intensity class, which is the band
 * that actually capsizes an open boat. Documented in RISK-RULES.md.
 *
 * Deliberately coarse: GDACS publishes wind-radii polygons per advisory timestamp, but only
 * sparsely, so a defensible parametric radius beats interpolating one polygon across three days.
 */
export const HAZARD_RADIUS_NM = [
  { minWindKmh: 165, radiusNm: 200, label: "Very Severe Cyclonic Storm" },
  { minWindKmh: 120, radiusNm: 160, label: "Severe Cyclonic Storm" },
  { minWindKmh: 88, radiusNm: 130, label: "Cyclonic Storm" },
  { minWindKmh: 62, radiusNm: 100, label: "Deep Depression" },
  { minWindKmh: 0, radiusNm: 80, label: "Depression" },
] as const;

export function hazardRadiusNm(maxWindKmh: number): { radiusNm: number; label: string } {
  const band = HAZARD_RADIUS_NM.find((b) => maxWindKmh >= b.minWindKmh) ?? HAZARD_RADIUS_NM[HAZARD_RADIUS_NM.length - 1];
  return { radiusNm: band.radiusNm, label: band.label };
}

/** Load the bundled Cyclone Dana replay. Real GDACS geometry for a real Red-alert cyclone. */
export function loadDanaReplay(): CycloneField {
  const f = danaFixture as unknown as Record<string, unknown>;
  return {
    eventid: Number(f.eventid),
    episodeid: Number(f.episodeid),
    eventname: String(f.eventname),
    alertlevel: String(f.alertlevel),
    country: String(f.country),
    maxWindKmh: Number(f.maxWindKmh),
    severitytext: String(f.severitytext),
    currentPosition: f.currentPosition as { lon: number; lat: number },
    track: f.track as TrackPoint[],
    cone: (f.cone as { ring: number[][] } | null) ?? null,
    upstreamSource: String(f.upstreamSource),
    kind: "ARCHIVE",
    isReplay: true,
    note: String(f.note),
  };
}

export function cycloneProvenance(c: CycloneField): Provenance {
  return prov(
    `GDACS tropical cyclone track (upstream: ${c.upstreamSource})`,
    c.kind,
    {
      // An archive replay is stamped with the EVENT date, so its age chip reads in days.
      fetchedAt: c.isReplay ? c.track[0]?.at : undefined,
      url: `https://www.gdacs.org/gdacsapi/api/polygons/getgeometry?eventtype=TC&eventid=${c.eventid}&episodeid=${c.episodeid}`,
      note: c.isReplay
        ? `Historical replay of ${c.eventname}. NOT a live alert. Official authority for the North Indian Ocean is RSMC New Delhi (IMD).`
        : "Machine-readable feed. GDACS aggregates NOAA/NHC and JTWC, not IMD. Official authority for the North Indian Ocean is RSMC New Delhi (IMD).",
    },
  );
}

export interface HazardArrival {
  /** Hours from `originIso` until damaging wind reaches the position. Null = never, in this track. */
  hoursUntil: number | null;
  /** The interpolated instant damaging wind arrives. */
  arrivalIso: string | null;
  /** Closest approach distance over the whole track, nm. */
  closestApproachNm: number;
  /** Whether the position is ALREADY inside the damaging-wind radius at `originIso`. */
  alreadyExposed: boolean;
  radiusNm: number;
  intensityLabel: string;
}

/**
 * When does damaging wind reach this position?
 *
 * Walks the timestamped track and finds the first segment on which the distance from the
 * position to the storm centre crosses the damaging-wind radius. Linear interpolation within
 * the crossing segment gives sub-6-hour resolution from 6-hourly advisory points.
 *
 * Deterministic. No model, no LLM.
 */
export function hazardArrival(
  position: { lon: number; lat: number },
  c: CycloneField,
  originIso: string,
): HazardArrival {
  const { radiusNm, label } = hazardRadiusNm(c.maxWindKmh);
  const origin = Date.parse(originIso);

  const samples = c.track.map((p) => ({
    t: Date.parse(p.at),
    nm: distanceKm([position.lon, position.lat], [p.lon, p.lat]) / KM_PER_NM,
  }));

  const closestApproachNm = samples.length ? Math.min(...samples.map((s) => s.nm)) : Infinity;

  // Only the future matters for a recall decision.
  const future = samples.filter((s) => s.t >= origin);
  if (!future.length) {
    return { hoursUntil: null, arrivalIso: null, closestApproachNm: +closestApproachNm.toFixed(1), alreadyExposed: false, radiusNm, intensityLabel: label };
  }

  if (future[0].nm <= radiusNm) {
    return {
      hoursUntil: 0, arrivalIso: new Date(future[0].t).toISOString(),
      closestApproachNm: +closestApproachNm.toFixed(1), alreadyExposed: true, radiusNm, intensityLabel: label,
    };
  }

  for (let i = 1; i < future.length; i++) {
    const a = future[i - 1];
    const b = future[i];
    if (b.nm <= radiusNm) {
      // Fraction along the segment at which distance crosses the radius.
      const span = a.nm - b.nm;
      const frac = span > 0 ? Math.max(0, Math.min(1, (a.nm - radiusNm) / span)) : 1;
      const tArrive = a.t + (b.t - a.t) * frac;
      return {
        hoursUntil: +((tArrive - origin) / 3_600_000).toFixed(2),
        arrivalIso: new Date(tArrive).toISOString(),
        closestApproachNm: +closestApproachNm.toFixed(1),
        alreadyExposed: false, radiusNm, intensityLabel: label,
      };
    }
  }

  return { hoursUntil: null, arrivalIso: null, closestApproachNm: +closestApproachNm.toFixed(1), alreadyExposed: false, radiusNm, intensityLabel: label };
}
