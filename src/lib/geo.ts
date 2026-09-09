/**
 * Geodesy helpers and text-to-coordinate resolution.
 * Location resolution deliberately uses only the bundled harbour gazetteer - no geocoding
 * dependency, so the demo works on a blocked venue network.
 */
import { point, lineString, distance, bearing, booleanPointInPolygon, nearestPointOnLine } from "@turf/turf";
import type { Feature, Polygon } from "geojson";
import { HARBOURS, COASTLINE, EEZ, type Harbour, type GeoFeature } from "./layers";
import type { ResolvedLocation } from "./types";

export const KM_PER_NM = 1.852;

export function distanceKm(a: [number, number], b: [number, number]): number {
  return distance(point(a), point(b), { units: "kilometers" });
}

export function bearingDeg(from: [number, number], to: [number, number]): number {
  const b = bearing(point(from), point(to));
  return (b + 360) % 360;
}

export function compass(deg: number): string {
  const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return dirs[Math.round(deg / 22.5) % 16];
}

export function polygonCentroid(coords: number[][][]): [number, number] {
  const ring = coords[0];
  let x = 0, y = 0;
  for (const [lon, lat] of ring.slice(0, -1)) { x += lon; y += lat; }
  const n = ring.length - 1;
  return [x / n, y / n];
}

export function insidePolygon(lon: number, lat: number, f: GeoFeature): boolean {
  if (f.geometry.type !== "Polygon") return false;
  return booleanPointInPolygon(point([lon, lat]), f as unknown as Feature<Polygon>);
}

/** Distance in km from a point to the nearest vertex-interpolated point on a LineString. */
export function distanceToLineKm(
  lon: number,
  lat: number,
  coords: number[][],
): { km: number; nearest: [number, number] } {
  const snapped = nearestPointOnLine(lineString(coords), point([lon, lat]), { units: "kilometers" });
  const c = snapped.geometry.coordinates as [number, number];
  return { km: snapped.properties.dist ?? distanceKm([lon, lat], c), nearest: c };
}

export function distanceToCoastKm(lon: number, lat: number): number {
  const line = COASTLINE.features[0].geometry;
  if (line.type !== "LineString") return NaN;
  return distanceToLineKm(lon, lat, line.coordinates).km;
}

export function insideEez(lon: number, lat: number): boolean {
  return insidePolygon(lon, lat, EEZ.features[0]);
}

const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9\s]/g, " ").replace(/\s+/g, " ").trim();

/** Find a harbour named anywhere in a free-text query. Longest match wins. */
export function findHarbourInText(text: string): Harbour | null {
  const q = norm(text);
  let best: { h: Harbour; len: number } | null = null;
  for (const h of HARBOURS) {
    const candidates = [h.name, h.id, ...h.aliases];
    for (const c of candidates) {
      const nc = norm(c).split(" (")[0];
      if (nc.length < 3) continue;
      if (q.includes(nc) && (!best || nc.length > best.len)) best = { h, len: nc.length };
    }
  }
  return best?.h ?? null;
}

/** Find TWO harbours in order of appearance, for "route from X to Y" queries. */
export function findHarbourPair(text: string): { from: Harbour; to: Harbour } | null {
  const q = norm(text);
  const hits: Array<{ h: Harbour; at: number; len: number }> = [];
  for (const h of HARBOURS) {
    let bestAt = -1, bestLen = 0;
    for (const c of [h.name, h.id, ...h.aliases]) {
      const nc = norm(c).split(" (")[0];
      if (nc.length < 3) continue;
      const at = q.indexOf(nc);
      if (at >= 0 && nc.length > bestLen) { bestAt = at; bestLen = nc.length; }
    }
    if (bestAt >= 0) hits.push({ h, at: bestAt, len: bestLen });
  }
  if (hits.length < 2) return null;
  hits.sort((a, b) => a.at - b.at);
  return { from: hits[0].h, to: hits[1].h };
}

const COORD_RE = /(-?\d{1,2}\.\d{2,6})\s*[,\s]\s*(-?\d{2,3}\.\d{2,6})/;

export function harbourToLocation(h: Harbour): ResolvedLocation {
  return {
    name: h.name,
    lat: h.lat,
    lon: h.lon,
    resolvedBy: "harbour-gazetteer",
    state: h.state,
    harbourId: h.id,
    inland: false,
  };
}

export const DEFAULT_LOCATION: ResolvedLocation = {
  name: "Chennai (Kasimedu)",
  lat: 13.1265,
  lon: 80.296,
  resolvedBy: "default",
  state: "Tamil Nadu",
  harbourId: "chennai",
  inland: false,
};

export interface Resolution {
  location: ResolvedLocation;
  destination?: ResolvedLocation;
  notice?: string;
}

/**
 * Resolve a location from free text, explicit coordinates, or a client-supplied device fix.
 * Falls back to the default demo location with an explicit notice - never silently guesses.
 */
export function resolveLocation(
  text: string,
  device?: { lat: number; lon: number } | null,
): Resolution {
  const pair = findHarbourPair(text);
  const routeish = /\b(route|from .* to |safest way|passage|sail to|navigate)\b/i.test(text);
  if (pair && routeish && pair.from.id !== pair.to.id) {
    return { location: harbourToLocation(pair.from), destination: harbourToLocation(pair.to) };
  }

  const m = text.match(COORD_RE);
  if (m) {
    const lat = Number(m[1]);
    const lon = Number(m[2]);
    if (Math.abs(lat) <= 40 && lon > 60 && lon < 100) {
      const coastKm = distanceToCoastKm(lon, lat);
      const marine = insideEez(lon, lat);
      return {
        location: {
          name: `${lat.toFixed(3)}, ${lon.toFixed(3)}`,
          lat, lon,
          resolvedBy: "explicit-coordinates",
          inland: !marine && coastKm > 25,
        },
      };
    }
  }

  const h = findHarbourInText(text);
  if (h) return { location: harbourToLocation(h) };

  if (device && Number.isFinite(device.lat) && Number.isFinite(device.lon)) {
    const coastKm = distanceToCoastKm(device.lon, device.lat);
    return {
      location: {
        name: "Your location",
        lat: device.lat, lon: device.lon,
        resolvedBy: "device",
        inland: !insideEez(device.lon, device.lat) && coastKm > 25,
      },
    };
  }

  return {
    location: DEFAULT_LOCATION,
    notice:
      "No location recognised in the query and no device position supplied. Falling back to the default demo location (Chennai). Name any of the 20 harbours in the gazetteer, or give coordinates as `lat, lon`.",
  };
}
