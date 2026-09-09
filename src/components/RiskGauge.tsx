"use client";

import type { Lang, RiskBand, RiskData } from "@/lib/types";
import { t } from "@/lib/i18n";

const BAND_COLOR: Record<RiskBand, string> = {
  SAFE: "var(--safe)",
  CAUTION: "var(--caution)",
  UNSAFE: "var(--unsafe)",
  // Grey, deliberately: UNKNOWN is an absence of judgement, not a level of danger.
  UNKNOWN: "#94a3b8",
};

/**
 * The risk gauge.
 *
 * Deliberately shows the measured value NEXT TO its threshold for every rule, both the ones
 * that fired and the ones that cleared. "Wave height 3.2 m exceeds the 2.5 m alert threshold"
 * is auditable in a way that a bare number is not - and the cleared list proves the engine
 * checked everything, not just what it reported.
 */
export default function RiskGauge({ risk, lang = "en" }: { risk: RiskData | null; lang?: Lang }) {
  if (!risk) {
    return (
      <div className="px-4 py-6 text-center text-sm" style={{ color: "var(--text-faint)" }}>
        {t(lang, "risk.waiting")}
      </div>
    );
  }

  const color = BAND_COLOR[risk.band];
  // Semicircular arc, 0 at left, 100 at right.
  const R = 62;
  const CIRC = Math.PI * R;
  const unknown = risk.band === "UNKNOWN";
  const filled = (unknown ? 1 : risk.score / 100) * CIRC;

  return (
    <div className="px-4 py-3">
      <div className="flex items-start gap-5">
        {/* gauge */}
        <div className="relative shrink-0" style={{ width: 150, height: 88 }}>
          <svg width="150" height="88" viewBox="0 0 150 88" aria-hidden>
            <path d="M 13 78 A 62 62 0 0 1 137 78" fill="none" stroke="var(--line)" strokeWidth="11" strokeLinecap="round" />
            <path
              d="M 13 78 A 62 62 0 0 1 137 78"
              fill="none"
              stroke={color}
              strokeWidth="11"
              strokeLinecap="round"
              strokeDasharray={unknown ? "3 7" : `${filled} ${CIRC}`}
              style={{ transition: "stroke-dasharray 700ms cubic-bezier(0.22,1,0.36,1), stroke 300ms" }}
            />
            {/* band boundaries at 33 and 66 */}
            {[33, 66].map((v) => {
              const a = Math.PI * (1 - v / 100);
              const x1 = 75 + Math.cos(a) * (R - 8);
              const y1 = 78 - Math.sin(a) * (R - 8);
              const x2 = 75 + Math.cos(a) * (R + 8);
              const y2 = 78 - Math.sin(a) * (R + 8);
              return <line key={v} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--bg)" strokeWidth="2.5" />;
            })}
          </svg>
          <div className="absolute inset-x-0 flex flex-col items-center" style={{ top: 30 }}>
            <div className="text-[38px] font-bold leading-none tabular-nums" style={{ color }}>
              {unknown ? "?" : risk.score}
            </div>
            <div className="text-[10px] tracking-widest" style={{ color: "var(--text-faint)" }}>
              {unknown ? "NO DATA" : "/ 100"}
            </div>
          </div>
        </div>

        {/* band + meta */}
        <div className="min-w-0 flex-1 pt-1">
          <div
            className="inline-block rounded-md px-3 py-1 text-[15px] font-bold tracking-wide"
            style={{ background: color, color: "#04121c" }}
          >
            {t(lang, `risk.band.${risk.band}`)}
          </div>
          <div className="mt-1.5 text-[13.5px] leading-snug" style={{ color: "var(--text-dim)" }}>
            {t(lang, `risk.bandNote.${risk.band}`)}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]" style={{ color: "var(--text-faint)" }}>
            <span>
              {t(lang, "risk.confidence")}: <span className="tabular-nums" style={{ color: "var(--text-dim)" }}>{Math.round(risk.confidence * 100)}%</span>
            </span>
            <span>
              {t(lang, "risk.completeness")}: <span className="tabular-nums" style={{ color: "var(--text-dim)" }}>{Math.round(risk.dataCompleteness * 100)}%</span>
            </span>
          </div>
        </div>
      </div>

      {risk.missing.length > 0 && (
        <div
          className="mt-2.5 rounded-md px-3 py-2 text-[12px] leading-snug"
          style={{ background: "rgba(148,163,184,0.12)", border: "1px solid rgba(148,163,184,0.4)", color: "#cbd5e1" }}
        >
          Missing agent data: <span className="mono">{risk.missing.join(", ")}</span>. The engine will not
          return a SAFE verdict without both sea state and wind.
        </div>
      )}

      {/* engine attribution - the answer to the hallucination question */}
      <div
        className="mt-3 rounded-md px-3 py-2 text-[12.5px] leading-snug"
        style={{ background: "rgba(56,189,248,0.08)", border: "1px solid rgba(56,189,248,0.28)", color: "#9fd8f5" }}
      >
        <span className="font-semibold">{t(lang, "risk.engine")}</span> · <span className="mono">{risk.engine}</span>
        <div className="mt-0.5" style={{ color: "var(--text-dim)" }}>{t(lang, "risk.engineNote")}</div>
      </div>

      {/* triggered rules */}
      <div className="mt-3">
        <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
          {t(lang, "risk.triggered")} ({risk.rules.length})
        </div>
        {risk.rules.length === 0 ? (
          <div className="rounded-md px-3 py-2 text-[13px]" style={{ background: "rgba(16,185,129,0.10)", border: "1px solid rgba(16,185,129,0.3)", color: "#6ee7b7" }}>
            {t(lang, "risk.none")}
          </div>
        ) : (
          <ul className="space-y-1.5">
            {risk.rules.map((r) => {
              const c = r.severity === "danger" ? "var(--unsafe)" : r.severity === "caution" ? "var(--caution)" : "var(--derived)";
              return (
                <li
                  key={r.id}
                  className="rounded-md px-3 py-2"
                  style={{ background: "rgba(255,255,255,0.03)", borderLeft: `3px solid ${c}` }}
                >
                  <div className="text-[14.5px] leading-snug" style={{ color: "var(--text)" }}>
                    {t(lang, `rules.${r.id}`, r.labelVars ?? {}) === `rules.${r.id}`
                      ? r.label
                      : t(lang, `rules.${r.id}`, r.labelVars ?? {})}
                  </div>
                  <div className="mt-1 flex flex-wrap items-baseline gap-x-2 text-[13px]">
                    <span className="mono font-semibold tabular-nums" style={{ color: c }}>
                      {r.measured} {r.unit}
                    </span>
                    <span style={{ color: "var(--text-faint)" }}>
                      {t(lang, "risk.measured")} · {t(lang, "risk.threshold")}
                    </span>
                    <span className="mono tabular-nums" style={{ color: "var(--text-dim)" }}>
                      {r.comparator} {r.threshold} {r.unit}
                    </span>
                    <span className="mono" style={{ color: "var(--text-faint)" }}>+{r.points}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] italic" style={{ color: "var(--text-faint)" }}>{r.source}</div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* cleared checks */}
      {risk.cleared.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
            {t(lang, "risk.cleared")} ({risk.cleared.length})
          </summary>
          <ul className="mt-1.5 space-y-1">
            {risk.cleared.map((c) => (
              <li key={c.id} className="flex items-baseline justify-between gap-3 px-1 text-[12px]">
                <span style={{ color: "var(--text-dim)" }}>
                  {t(lang, `rulesCleared.${c.id}`) === `rulesCleared.${c.id}` ? c.label : t(lang, `rulesCleared.${c.id}`)}
                </span>
                <span className="mono shrink-0 tabular-nums" style={{ color: "var(--safe)" }}>
                  {c.measured} {c.unit} <span style={{ color: "var(--text-faint)" }}>&lt; {c.threshold}</span>
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="mt-3 text-[11px]" style={{ color: "var(--text-faint)" }}>
        {t(lang, "risk.vessel")}: {risk.vesselClass}
      </div>
    </div>
  );
}
