/**
 * Offline fallback snapshots.
 *
 * These are VERBATIM captures of real Open-Meteo responses, taken while building the
 * prototype. They exist for exactly one reason: a venue network that dies mid-demo.
 *
 * Rules enforced here and in the UI:
 *  - a snapshot is only ever used after a live call has actually failed or timed out
 *  - a snapshot is ALWAYS surfaced with kind "SNAPSHOT" / "ARCHIVE" and its capture time
 *  - a snapshot from a different location than the query is refused beyond 250 km, so we
 *    never quietly answer a Kochi question with Chennai's sea state
 */
import chennai from "@data/snapshots/chennai.json";
import kanyakumari from "@data/snapshots/kanyakumari.json";
import paradip from "@data/snapshots/paradip.json";
import rameswaram from "@data/snapshots/rameswaram.json";
import puducherry from "@data/snapshots/puducherry.json";
import kochi from "@data/snapshots/kochi.json";
import paradipDana from "@data/snapshots/paradip-dana.json";
import { distanceKm } from "./geo";
import type { ResolvedLocation } from "./types";

export interface Snapshot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  capturedAt: string;
  kind: string;
  event?: string;
  note: string;
  marine: unknown;
  forecast: unknown;
}

const ALL = [chennai, kanyakumari, paradip, rameswaram, puducherry, kochi, paradipDana] as unknown as Snapshot[];

/** Beyond this, a snapshot from a neighbouring port is not a defensible stand-in. */
export const MAX_SNAPSHOT_DISTANCE_KM = 250;

export function getSnapshotById(id: string): Snapshot | null {
  return ALL.find((s) => s.id === id) ?? null;
}

/**
 * Find the best snapshot for a location. A scenario may pin a specific snapshot by id
 * (the cyclone replay does this); otherwise we take the nearest within 250 km.
 */
export function getSnapshot(location: ResolvedLocation, scenarioSnapshotId?: string): Snapshot | null {
  if (scenarioSnapshotId) {
    const pinned = getSnapshotById(scenarioSnapshotId);
    if (pinned) return pinned;
  }
  let best: { s: Snapshot; km: number } | null = null;
  for (const s of ALL) {
    if (s.kind === "ARCHIVE") continue; // never auto-substitute a cyclone replay for live conditions
    const km = distanceKm([location.lon, location.lat], [s.lon, s.lat]);
    if (!best || km < best.km) best = { s, km };
  }
  if (!best || best.km > MAX_SNAPSHOT_DISTANCE_KM) return null;
  return best.s;
}

export function listSnapshots() {
  return ALL.map((s) => ({ id: s.id, name: s.name, capturedAt: s.capturedAt, kind: s.kind, event: s.event }));
}
