import type { DataKind, Provenance } from "./types";

/** Build a provenance record. Nothing renders in ORCA without one of these attached. */
export function prov(
  source: string,
  kind: DataKind,
  opts: { fetchedAt?: string; ageSeconds?: number; url?: string; note?: string } = {},
): Provenance {
  const fetchedAt = opts.fetchedAt ?? new Date().toISOString();
  const ageSeconds =
    opts.ageSeconds ?? Math.max(0, Math.round((Date.now() - Date.parse(fetchedAt)) / 1000));
  return { source, kind, fetchedAt, ageSeconds, url: opts.url, note: opts.note };
}
