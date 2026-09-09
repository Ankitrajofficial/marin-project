/**
 * Process-local TTL cache. Repeated demo runs against the same coordinate return instantly
 * instead of re-hitting Open-Meteo, which matters when a judging panel re-runs a scenario.
 * Production equivalent: Redis with the same key shape.
 */

interface Entry<T> {
  value: T;
  storedAt: number;
  expiresAt: number;
}

const TTL_MS = 10 * 60 * 1000;
const store = new Map<string, Entry<unknown>>();

export interface CacheRead<T> {
  value: T;
  /** Seconds since the value was actually fetched. Feeds the provenance age stamp. */
  ageSeconds: number;
  hit: boolean;
  storedAtISO: string;
}

export function cacheGet<T>(key: string): CacheRead<T> | null {
  const e = store.get(key) as Entry<T> | undefined;
  if (!e) return null;
  if (Date.now() > e.expiresAt) {
    store.delete(key);
    return null;
  }
  return {
    value: e.value,
    ageSeconds: Math.round((Date.now() - e.storedAt) / 1000),
    hit: true,
    storedAtISO: new Date(e.storedAt).toISOString(),
  };
}

export function cacheSet<T>(key: string, value: T, ttlMs: number = TTL_MS): void {
  store.set(key, { value, storedAt: Date.now(), expiresAt: Date.now() + ttlMs });
}

export function cacheClear(): number {
  const n = store.size;
  store.clear();
  return n;
}

export function cacheStats() {
  return { entries: store.size, keys: [...store.keys()] };
}
