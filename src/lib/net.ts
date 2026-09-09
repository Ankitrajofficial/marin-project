/**
 * Every outbound call in ORCA goes through here. Two demo-day rules are enforced:
 *   1. Nothing may hang. Every fetch carries an AbortController timeout.
 *   2. Nothing may throw past the agent boundary. Failures return a typed result.
 */

export interface FetchOutcome<T> {
  ok: boolean;
  data: T | null;
  status: "ok" | "error" | "timeout";
  error?: string;
  durationMs: number;
  httpStatus?: number;
}

export const DEFAULT_TIMEOUT_MS = 6000;

export async function fetchJson<T>(
  url: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<FetchOutcome<T>> {
  const started = Date.now();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      headers: { accept: "application/json", "user-agent": "ORCA-SIH2026-prototype" },
    });
    if (!res.ok) {
      return {
        ok: false,
        data: null,
        status: "error",
        error: `HTTP ${res.status} ${res.statusText}`,
        httpStatus: res.status,
        durationMs: Date.now() - started,
      };
    }
    const data = (await res.json()) as T;
    return { ok: true, data, status: "ok", durationMs: Date.now() - started, httpStatus: res.status };
  } catch (err) {
    const aborted = err instanceof Error && err.name === "AbortError";
    return {
      ok: false,
      data: null,
      status: aborted ? "timeout" : "error",
      error: aborted ? `Timed out after ${timeoutMs}ms` : err instanceof Error ? err.message : String(err),
      durationMs: Date.now() - started,
    };
  } finally {
    clearTimeout(timer);
  }
}
