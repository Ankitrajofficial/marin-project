/**
 * GEOSPATIAL AGENT
 * Tools: Turf.js against the CACHED GeoJSON layers in /data.
 * Answers: nearest PFZ, nearest harbour, distance to the nearest maritime boundary,
 * containment in a marine protected area, EEZ containment, distance to coast.
 *
 * Everything this agent returns is CACHED or DERIVED, never LIVE, and it says so.
 * Production equivalent: PostGIS with H3 indexing over the INCOIS PFZ advisory feed and
 * the authoritative boundary datasets.
 */
import { PFZ, IMBL, MPA, HARBOURS } from "@/lib/layers";
import {
  distanceKm, bearingDeg, polygonCentroid, insidePolygon, distanceToLineKm,
  distanceToCoastKm, insideEez, KM_PER_NM,
} from "@/lib/geo";
import { prov } from "@/lib/provenance";
import type {
  AgentResult, BoundaryHit, GeospatialData, MpaHit, PfzHit, Provenance, ResolvedLocation, ToolCall,
} from "@/lib/types";

const IMBL_CRITICAL_NM = 2;
const IMBL_WARNING_NM = 5;
const IMBL_ADVISORY_NM = 10;

function severityFor(nm: number): BoundaryHit["severity"] {
  if (nm <= IMBL_CRITICAL_NM) return "critical";
  if (nm <= IMBL_WARNING_NM) return "warning";
  if (nm <= IMBL_ADVISORY_NM) return "advisory";
  return "clear";
}

export function runGeospatialAgent(location: ResolvedLocation): AgentResult<GeospatialData> {
  const t0 = Date.now();
  const startedAt = new Date().toISOString();
  const toolCalls: ToolCall[] = [];
  const provenance: Provenance[] = [];
  const here: [number, number] = [location.lon, location.lat];

  // ---- PFZ candidates --------------------------------------------------------
  const tPfz = Date.now();
  const pfzCandidates: PfzHit[] = PFZ.features.map((f) => {
    const c = polygonCentroid((f.geometry as { coordinates: number[][][] }).coordinates);
    const p = f.properties as Record<string, unknown>;
    return {
      id: String(p.id),
      nearestHarbour: String(p.nearest_harbour),
      distanceKm: +distanceKm(here, c).toFixed(1),
      bearingDeg: +bearingDeg(here, c).toFixed(0),
      centroid: c,
      score: Number(p.score),
      confidence: String(p.confidence),
      sstC: Number(p.sst_c),
      chlorophyll: Number(p.chlorophyll_a_mg_m3),
      depthBandM: String(p.depth_band_m),
      label: String(p.label),
    };
  }).sort((a, b) => a.distanceKm - b.distanceKm);

  toolCalls.push({
    tool: "turf.distance over pfz-zones.json",
    status: "ok",
    durationMs: Date.now() - tPfz,
    summary: `${pfzCandidates.length} candidate zones ranked by distance; nearest ${pfzCandidates[0]?.distanceKm ?? "n/a"} km`,
    raw: { layerMeta: PFZ.meta, nearest3: pfzCandidates.slice(0, 3) },
  });
  provenance.push(
    prov("ORCA PFZ demo derivation (SST + chlorophyll-a proxy)", "CACHED", {
      fetchedAt: `${String(PFZ.meta.generated)}T00:00:00+05:30`,
      note: "NOT an INCOIS advisory. Demo derivation. Production consumes the INCOIS PFZ advisory feed.",
    }),
  );

  // ---- nearest harbour -------------------------------------------------------
  const tH = Date.now();
  let nearestHarbour: GeospatialData["nearestHarbour"] = null;
  for (const h of HARBOURS) {
    const km = distanceKm(here, [h.lon, h.lat]);
    if (!nearestHarbour || km < nearestHarbour.distanceKm) {
      nearestHarbour = { id: h.id, name: h.name, distanceKm: +km.toFixed(1), lat: h.lat, lon: h.lon };
    }
  }
  toolCalls.push({
    tool: "gazetteer.nearest over harbours.json",
    status: "ok",
    durationMs: Date.now() - tH,
    summary: nearestHarbour ? `${nearestHarbour.name} at ${nearestHarbour.distanceKm} km` : "no harbour found",
    raw: nearestHarbour,
  });

  // ---- maritime boundaries ---------------------------------------------------
  const tB = Date.now();
  const boundaries: BoundaryHit[] = IMBL.features
    .filter((f) => f.geometry.type === "LineString")
    .map((f) => {
      const coords = (f.geometry as { coordinates: number[][] }).coordinates;
      const { km, nearest } = distanceToLineKm(location.lon, location.lat, coords);
      const nm = km / KM_PER_NM;
      const p = f.properties as Record<string, unknown>;
      return {
        id: String(p.id),
        name: String(p.name),
        distanceKm: +km.toFixed(1),
        distanceNm: +nm.toFixed(2),
        agreement: String(p.agreement),
        accuracy: String(p.accuracy),
        severity: severityFor(nm),
        nearestPoint: nearest,
      };
    })
    .sort((a, b) => a.distanceKm - b.distanceKm);

  const boundary = boundaries[0] ?? null;
  toolCalls.push({
    tool: "turf.nearestPointOnLine over imbl.json",
    status: "ok",
    durationMs: Date.now() - tB,
    summary: boundary
      ? `Nearest: ${boundary.name} at ${boundary.distanceNm} nm (${boundary.severity})`
      : "no boundary data",
    raw: { buffersNm: { critical: IMBL_CRITICAL_NM, warning: IMBL_WARNING_NM, advisory: IMBL_ADVISORY_NM }, boundaries },
  });
  provenance.push(
    prov("ORCA approximate IMBL turning points", "CACHED", {
      fetchedAt: `${String(IMBL.meta.generated)}T00:00:00+05:30`,
      note: String(IMBL.meta.warning),
    }),
  );

  // ---- marine protected areas ------------------------------------------------
  const tM = Date.now();
  const mpas: MpaHit[] = MPA.features.map((f) => {
    const p = f.properties as Record<string, unknown>;
    const inside = insidePolygon(location.lon, location.lat, f);
    const c = polygonCentroid((f.geometry as { coordinates: number[][][] }).coordinates);
    return {
      id: String(p.id),
      name: String(p.name),
      inside,
      distanceKm: +distanceKm(here, c).toFixed(1),
      restriction: String(p.restriction),
      rule: String(p.rule),
      designation: String(p.designation),
    };
  }).sort((a, b) => (a.inside === b.inside ? a.distanceKm - b.distanceKm : a.inside ? -1 : 1));

  const mpa = mpas[0] ?? null;
  toolCalls.push({
    tool: "turf.booleanPointInPolygon over mpa.json",
    status: "ok",
    durationMs: Date.now() - tM,
    summary: mpa?.inside ? `INSIDE ${mpa.name}` : mpa ? `Nearest MPA ${mpa.name} at ${mpa.distanceKm} km` : "no MPA data",
    raw: mpas.slice(0, 3),
  });
  provenance.push(
    prov("ORCA simplified marine protected area polygons", "CACHED", {
      fetchedAt: `${String(MPA.meta.generated)}T00:00:00+05:30`,
      note: String(MPA.meta.warning),
    }),
  );

  // ---- EEZ + coast -----------------------------------------------------------
  const eezIn = insideEez(location.lon, location.lat);
  const coastKm = +distanceToCoastKm(location.lon, location.lat).toFixed(1);
  toolCalls.push({
    tool: "turf.booleanPointInPolygon over eez.json",
    status: "ok",
    durationMs: 1,
    summary: `${eezIn ? "Inside" : "Outside"} the indicative Indian EEZ; ${coastKm} km from the coastline`,
    raw: { insideEez: eezIn, distanceToCoastKm: coastKm },
  });

  const data: GeospatialData = {
    nearestPfz: pfzCandidates[0] ?? null,
    pfzCandidates: pfzCandidates.slice(0, 5),
    nearestHarbour,
    boundary,
    boundaries: boundaries.slice(0, 3),
    mpa,
    mpas: mpas.slice(0, 3),
    insideEez: eezIn,
    distanceToCoastKm: coastKm,
  };

  return {
    agent: "geospatial",
    task: `Locate PFZ, boundaries and protected areas around ${location.name}`,
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
