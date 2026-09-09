"use client";

import { useCallback, useEffect, useState } from "react";
import type { Lang, Provenance } from "@/lib/types";
import type { TriageResult, TriageSummary, TriageBand } from "@/agents/triage";
import { ProvenanceRow } from "./ProvenanceChip";

interface CurvePoint {
  at: string;
  label: string;
  hoursBeforeLandfall: number;
  unreachable: number;
  counts: Record<TriageBand, number>;
}

interface TriageResponse {
  summary: TriageSummary;
  seaState: { waveHeightM: number; source: string; kind: string; measuredAt: string; note: string };
  cyclone: {
    eventname: string; alertlevel: string; maxWindKmh: number; severitytext: string;
    isReplay: boolean; upstreamSource: string; note: string;
    officialAuthority: { name: string; url: string; label: string; note: string };
    attribution: string;
  };
  fleetMeta: { size: number; simulated: boolean; why: string; offshoreNm: { median: number; max: number } };
  landfallIso: string;
  recallTimingCurve: CurvePoint[];
  diversionNote: string;
  recallList: TriageResult[];
  truncated: boolean;
  provenance: Provenance[];
}

const BAND_COLOR: Record<TriageBand, string> = {
  CRITICAL: "var(--unsafe)",
  URGENT: "var(--caution)",
  WATCH: "#38bdf8",
  CLEAR: "var(--safe)",
};

/**
 * Vessel recall triage — the Disaster Management view.
 *
 * Not an advisory a fisherman reads. An operational triage a coastal control room acts on:
 * which boat gets the call first, and what it costs to decide late.
 */
export default function RecallPanel({ lang = "en" }: { lang?: Lang }) {
  const [data, setData] = useState<TriageResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [at, setAt] = useState<string | null>(null);

  const load = useCallback(async (when: string | null) => {
    setLoading(true);
    try {
      const q = new URLSearchParams({ fleet: "250", limit: "25" });
      if (when) q.set("at", when);
      const r = await fetch(`/api/triage?${q}`);
      setData(await r.json());
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(at);
  }, [at, load]);

  if (!data) {
    return (
      <div className="px-4 py-6 text-center text-[13px]" style={{ color: "var(--text-faint)" }}>
        {loading ? "Solving recall triage…" : "Triage unavailable."}
      </div>
    );
  }

  const { summary: s, cyclone: c, recallTimingCurve: curve } = data;
  const maxUnreachable = Math.max(...curve.map((p) => p.unreachable), 1);
  const earliest = curve[0];
  const worst = curve.reduce((m, p) => (p.unreachable > m.unreachable ? p : m), curve[0]);

  return (
    <div className="px-4 py-3">
      {/* storm header */}
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
          Vessel recall triage
        </h3>
        <span
          className="rounded px-1.5 py-[2px] text-[10px] font-bold"
          style={{ background: c.alertlevel === "Red" ? "var(--unsafe)" : "var(--caution)", color: "#04121c" }}
        >
          {c.alertlevel.toUpperCase()}
        </span>
        <span className="text-[12px]" style={{ color: "var(--text)" }}>
          {c.eventname} · {c.maxWindKmh.toFixed(0)} km/h
        </span>
        {loading && <span className="text-[11px]" style={{ color: "var(--accent)" }}>solving…</span>}
      </div>

      {c.isReplay && (
        <div
          className="mt-1.5 rounded-md px-2.5 py-1.5 text-[11px] leading-snug"
          style={{ background: "rgba(167,139,250,0.12)", border: "1px solid rgba(167,139,250,0.45)", color: "#c4b5fd" }}
        >
          <b>HISTORICAL REPLAY</b> — real GDACS track for {c.eventname}, not a live alert.
          <div className="mt-0.5" style={{ color: "var(--text-dim)" }}>
            Feed: GDACS (upstream {c.upstreamSource}). Official authority is{" "}
            <a href={c.officialAuthority.url} target="_blank" rel="noreferrer" className="underline" style={{ color: "#c4b5fd" }}>
              {c.officialAuthority.label}
            </a>
            .
          </div>
        </div>
      )}

      {/* the money shot: cost of deciding late */}
      <div className="mt-3">
        <div className="mb-1 flex items-baseline justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
            Cost of deciding late
          </span>
          <span className="text-[11px]" style={{ color: "var(--text-faint)" }}>
            boats with no reachable harbour
          </span>
        </div>
        <div className="space-y-[2px]">
          {curve.map((p) => {
            const active = at ? p.at === at : p === earliest;
            return (
              <button
                key={p.at}
                onClick={() => setAt(p.at)}
                className="flex w-full items-center gap-2 rounded px-1 py-[1px] text-left transition-colors hover:bg-white/5"
                title={`Solve the recall list at ${p.label}`}
              >
                <span
                  className="mono w-[42px] shrink-0 text-right text-[10.5px] tabular-nums"
                  style={{ color: active ? "var(--accent)" : "var(--text-faint)" }}
                >
                  {p.hoursBeforeLandfall >= 0 ? `T-${p.hoursBeforeLandfall}h` : `T+${-p.hoursBeforeLandfall}h`}
                </span>
                <span className="h-[9px] flex-1 overflow-hidden rounded-sm" style={{ background: "rgba(255,255,255,0.05)" }}>
                  <span
                    className="block h-full rounded-sm"
                    style={{
                      width: `${(p.unreachable / maxUnreachable) * 100}%`,
                      background: active ? "var(--unsafe)" : "rgba(244,63,94,0.5)",
                    }}
                  />
                </span>
                <span
                  className="mono w-[26px] shrink-0 text-right text-[11px] font-semibold tabular-nums"
                  style={{ color: active ? "var(--unsafe)" : "var(--text-dim)" }}
                >
                  {p.unreachable}
                </span>
              </button>
            );
          })}
        </div>
        <div className="mt-1.5 text-[11.5px] leading-snug" style={{ color: "#fca5a5" }}>
          Deciding at T-{earliest.hoursBeforeLandfall}h strands <b>{earliest.unreachable}</b> boats. Waiting to
          T-{worst.hoursBeforeLandfall}h strands <b>{worst.unreachable}</b>.{" "}
          <b>{worst.unreachable - earliest.unreachable} more boats</b> with nowhere to go.
        </div>
      </div>

      {/* band counts at the selected decision time */}
      <div className="mt-3 grid grid-cols-4 gap-1.5">
        {(["CRITICAL", "URGENT", "WATCH", "CLEAR"] as TriageBand[]).map((b) => (
          <div key={b} className="rounded-md px-2 py-1.5 text-center" style={{ background: "rgba(255,255,255,0.03)", borderTop: `2px solid ${BAND_COLOR[b]}` }}>
            <div className="text-[17px] font-bold tabular-nums" style={{ color: BAND_COLOR[b] }}>{s.counts[b]}</div>
            <div className="text-[9.5px] uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>{b}</div>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px]" style={{ color: "var(--text-faint)" }}>
        <span>decision <span className="mono" style={{ color: "var(--text-dim)" }}>{s.originIso.slice(0, 16)}Z</span></span>
        <span>hazard radius <span className="mono" style={{ color: "var(--text-dim)" }}>{s.hazardRadiusNm} nm</span></span>
        <span>sea <span className="mono" style={{ color: "var(--text-dim)" }}>{data.seaState.waveHeightM} m</span></span>
        <span>solver <span className="mono" style={{ color: "var(--text-dim)" }}>{s.solver}</span></span>
      </div>

      {/* recall list */}
      <div className="mt-3">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
          Prioritised recall order
        </div>
        <ol className="space-y-1">
          {data.recallList.slice(0, 12).map((r, i) => (
            <li
              key={r.vessel.id}
              className="rounded-md px-2.5 py-1.5"
              style={{ background: "rgba(255,255,255,0.03)", borderLeft: `3px solid ${BAND_COLOR[r.band]}` }}
            >
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="mono text-[11px]" style={{ color: "var(--text-faint)" }}>{String(i + 1).padStart(2, "0")}</span>
                <span className="mono text-[12.5px] font-semibold" style={{ color: "var(--text)" }}>{r.vessel.regNo}</span>
                <span className="text-[10.5px] uppercase tracking-wide" style={{ color: BAND_COLOR[r.band] }}>{r.band}</span>
                <span className="ml-auto mono text-[11px] tabular-nums" style={{ color: "var(--text-dim)" }}>
                  {r.marginH !== null ? `margin ${r.marginH.toFixed(1)} h` : `hazard ${r.timeToHazardH?.toFixed(1)} h`}
                </span>
              </div>
              <div className="mt-0.5 text-[11.5px] leading-snug" style={{ color: "var(--text-dim)" }}>
                {r.vessel.class} · crew {r.vessel.crew} · {r.effectiveSpeedKn} kn effective ({r.seaStateLabel})
              </div>
              <div className="mt-0.5 text-[11.5px] leading-snug" style={{ color: "var(--text)" }}>{r.reason}</div>
            </li>
          ))}
        </ol>
      </div>

      {/* honesty block */}
      <div
        className="mt-3 rounded-md px-2.5 py-2 text-[11px] leading-snug"
        style={{ background: "rgba(245,158,11,0.10)", border: "1px solid rgba(245,158,11,0.4)", color: "#fcd34d" }}
      >
        <b>Fleet positions are SIMULATED</b> ({data.fleetMeta.size} vessels, median{" "}
        {data.fleetMeta.offshoreNm.median} nm offshore).
        <div className="mt-0.5" style={{ color: "var(--text-dim)" }}>{data.fleetMeta.why}</div>
        <div className="mt-1" style={{ color: "var(--text-dim)" }}>
          The cyclone track, harbour set, geodesy, sea state and hazard timing are all real.
        </div>
      </div>

      <div className="mt-2 text-[10.5px] italic leading-snug" style={{ color: "var(--text-faint)" }}>
        {data.diversionNote}
      </div>

      <div className="mt-2">
        <ProvenanceRow items={data.provenance} lang={lang} />
      </div>
      <div className="mt-1.5 text-[10px]" style={{ color: "var(--text-faint)" }}>
        {c.attribution}
      </div>
    </div>
  );
}
