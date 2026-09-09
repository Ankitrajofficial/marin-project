/**
 * SIMULATED FLEET.
 *
 * Read this before showing the recall list to anyone.
 *
 * Vessel positions here are SYNTHETIC. There is no public position feed for small Indian
 * fishing vessels, because most of them carry no AIS transponder at all — AIS is mandatory for
 * larger vessels, not for the sub-20 m open and half-decked boats that make up the bulk of the
 * fleet. So there is no dataset to fetch, and anyone claiming a live one for this class of boat
 * is wrong.
 *
 * That absence is not a hole in this demo, it IS the problem the deck proposes to solve: in
 * production the recall list is driven by last-known position from the state fisheries
 * registration database plus VHF check-in plus the transponder rollout, which is precisely
 * what coastal authorities already partially hold.
 *
 * So: the fleet is simulated and labelled SIMULATED everywhere it appears. Everything the
 * solver does WITH those positions — the geodesy, the harbour set, the cyclone track, the
 * hazard timing — is real. Swap this module for a registry query and nothing downstream changes.
 *
 * Positions are drawn from a seeded generator so a demo is reproducible run to run.
 */
import { distanceKm } from "./geo";
import { HARBOURS } from "./layers";

export interface Vessel {
  id: string;
  /** Registration-style identifier, cosmetic. */
  regNo: string;
  lon: number;
  lat: number;
  /** Rated cruise speed in knots, before sea-state derating. */
  ratedSpeedKn: number;
  class: string;
  crew: number;
  /** Harbour the vessel sailed from, which is how a registry would know it is at sea. */
  homePort: string;
  /** Always true for this module. Kept explicit so no caller can forget. */
  simulated: true;
}

/**
 * Vessel classes with realistic rated speeds for the Indian small-boat fleet.
 * Speeds are cruise, not maximum.
 */
const CLASSES = [
  { class: "Traditional / non-motorised catamaran", speedKn: 3.5, crew: 3, weight: 0.18 },
  { class: "Motorised FRP boat (OBM)", speedKn: 6.5, crew: 4, weight: 0.42 },
  { class: "Mechanised gillnetter", speedKn: 8.0, crew: 6, weight: 0.24 },
  { class: "Mechanised trawler (< 20 m)", speedKn: 9.5, crew: 8, weight: 0.16 },
] as const;

/** Deterministic PRNG (mulberry32) so the same seed gives the same fleet every run. */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export interface FleetOptions {
  size?: number;
  seed?: number;
  /** Restrict home ports to these coasts, e.g. ["east"] for a Bay of Bengal cyclone. */
  coasts?: string[];
  /** Fishing grounds sit offshore of the home port; nm range. */
  minOffshoreNm?: number;
  maxOffshoreNm?: number;
}

/**
 * Generate a fleet at sea off the given coasts.
 *
 * Vessels are placed offshore of a real home port, seaward, at a distance drawn from a
 * distribution weighted toward the inshore grounds where most of the fleet actually works —
 * a uniform scatter would put implausibly many small boats 100 nm out.
 */
export function generateFleet(opts: FleetOptions = {}): Vessel[] {
  const size = opts.size ?? 120;
  const rand = rng(opts.seed ?? 26176);
  const coasts = opts.coasts;
  const minNm = opts.minOffshoreNm ?? 4;
  const maxNm = opts.maxOffshoreNm ?? 90;

  const ports = HARBOURS.filter((h) => !coasts || coasts.includes(h.coast));
  if (!ports.length) return [];

  // Seaward direction: push away from the peninsular pseudo-centroid, same convention as the
  // PFZ layer generator.
  const CX = 79.0, CY = 21.0;

  const pickClass = () => {
    const r = rand();
    let acc = 0;
    for (const c of CLASSES) {
      acc += c.weight;
      if (r <= acc) return c;
    }
    return CLASSES[1];
  };

  const vessels: Vessel[] = [];
  for (let i = 0; i < size; i++) {
    const port = ports[Math.floor(rand() * ports.length)];
    const cls = pickClass();

    // Weight toward inshore: square the uniform draw.
    const frac = rand() ** 2;
    const offshoreNm = minNm + frac * (maxNm - minNm);
    const offshoreDeg = (offshoreNm * 1.852) / 111.0;

    let ux = port.lon - CX, uy = port.lat - CY;
    const m = Math.hypot(ux, uy) || 1;
    ux /= m; uy /= m;

    // Spread along the coast as well as offshore, so the fleet is not a radial starburst.
    const jitter = (rand() - 0.5) * 1.1;
    const lon = port.lon + (offshoreDeg * ux) / Math.cos((port.lat * Math.PI) / 180) - uy * jitter;
    const lat = port.lat + offshoreDeg * uy + ux * jitter;

    vessels.push({
      id: `V${String(i + 1).padStart(4, "0")}`,
      regNo: `IND-${port.id.slice(0, 3).toUpperCase()}-${String(1000 + Math.floor(rand() * 8999))}`,
      lon: +lon.toFixed(4),
      lat: +lat.toFixed(4),
      ratedSpeedKn: +(cls.speedKn * (0.85 + rand() * 0.3)).toFixed(1),
      class: cls.class,
      crew: cls.crew + Math.floor(rand() * 3) - 1,
      homePort: port.name,
      simulated: true,
    });
  }
  return vessels;
}

/** Sanity helper for the verification pass: how far offshore did we actually put them? */
export function fleetStats(fleet: Vessel[]) {
  const offshore = fleet.map((v) => {
    let best = Infinity;
    for (const h of HARBOURS) best = Math.min(best, distanceKm([v.lon, v.lat], [h.lon, h.lat]) / 1.852);
    return best;
  });
  const speeds = fleet.map((v) => v.ratedSpeedKn);
  const q = (a: number[], p: number) => {
    const s = [...a].sort((x, y) => x - y);
    return +s[Math.floor(p * (s.length - 1))].toFixed(1);
  };
  return {
    size: fleet.length,
    offshoreNm: { min: q(offshore, 0), median: q(offshore, 0.5), p90: q(offshore, 0.9), max: q(offshore, 1) },
    ratedSpeedKn: { min: q(speeds, 0), median: q(speeds, 0.5), max: q(speeds, 1) },
    classes: [...new Set(fleet.map((v) => v.class))].length,
  };
}
