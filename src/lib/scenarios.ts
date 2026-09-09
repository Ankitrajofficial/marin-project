import type { ArchiveWindow, Lang } from "./types";

export interface Scenario {
  id: string;
  title: string;
  subtitle: string;
  query: string;
  language: Lang;
  /** Set for the cyclone scenario: replays a real past event from the Open-Meteo archive. */
  archive?: ArchiveWindow;
  /** Pins the offline fallback snapshot for this scenario. */
  snapshotId?: string;
  zoom?: number;
}

/**
 * Six pinned demo scenarios. Every one runs the REAL pipeline against the live APIs.
 * If the network is unavailable each falls back to a snapshot captured from a real prior
 * response, always visibly labelled - never a silent fake.
 *
 * Scenario 2 is different by design: today's Odisha coast is usually calm, so instead of
 * inventing a cyclone it replays REAL archived conditions from Cyclone Dana's landfall
 * (24-25 Oct 2024), fetched live from the Open-Meteo historical archive and labelled as
 * a historical replay throughout the UI.
 */
export const SCENARIOS: Scenario[] = [
  {
    id: "safe-kanyakumari",
    title: "Safe fishing day",
    subtitle: "Kanyakumari, Tamil Nadu",
    query: "Is it safe to go to sea tomorrow morning from Kanyakumari?",
    language: "en",
    snapshotId: "kanyakumari",
    zoom: 8,
  },
  {
    id: "cyclone-paradip",
    title: "Cyclone warning",
    subtitle: "Paradip, Odisha — historical replay of Cyclone Dana",
    query: "Any cyclone or lightning alerts near Paradip? Is it safe to sail?",
    language: "en",
    archive: { startDate: "2024-10-24", endDate: "2024-10-25", label: "Cyclone Dana landfall, 24-25 Oct 2024" },
    snapshotId: "paradip-dana",
    zoom: 7,
  },
  {
    id: "pfz-chennai",
    title: "Nearest fishing zone",
    subtitle: "Chennai, Tamil Nadu",
    query: "Where is the nearest Potential Fishing Zone today from Chennai?",
    language: "en",
    snapshotId: "chennai",
    zoom: 8,
  },
  {
    id: "geofence-rameswaram",
    title: "Geofence proximity warning",
    subtitle: "Off Rameswaram, near the India–Sri Lanka IMBL",
    query: "Am I close to any restricted or international boundary water? My position is 9.3200, 79.4800",
    language: "en",
    snapshotId: "rameswaram",
    zoom: 9,
  },
  {
    id: "route-chennai-puducherry",
    title: "Safe route",
    subtitle: "Chennai → Puducherry",
    query: "What is the safest route from Chennai to Puducherry for a small fishing vessel?",
    language: "en",
    snapshotId: "chennai",
    zoom: 8,
  },
  {
    id: "productivity-kerala",
    title: "Productivity decline",
    subtitle: "Kerala coast, off Kochi",
    query: "Why has fish productivity dropped near this coast at Kochi?",
    language: "en",
    snapshotId: "kochi",
    zoom: 8,
  },
];

export function getScenario(id: string | null | undefined): Scenario | null {
  if (!id) return null;
  return SCENARIOS.find((s) => s.id === id) ?? null;
}
