"use client";

import { useState } from "react";
import type { AgentName, AgentResult, EvidenceItem, Lang, QueryPlan, ToolCall } from "@/lib/types";
import { t } from "@/lib/i18n";
import ProvenanceChip from "./ProvenanceChip";

export interface TraceStep {
  key: string;
  agent: AgentName;
  task: string;
  index: number;
  status: "running" | "ok" | "degraded" | "failed";
  result?: AgentResult;
}

const AGENT_META: Record<AgentName, { label: string; glyph: string; color: string }> = {
  planner: { label: "Planner", glyph: "◇", color: "#c4b5fd" },
  geospatial: { label: "Geospatial", glyph: "◉", color: "#7dd3fc" },
  ocean: { label: "Ocean", glyph: "≈", color: "#22d3ee" },
  weather: { label: "Weather", glyph: "☁", color: "#60a5fa" },
  route: { label: "Route", glyph: "⤳", color: "#f0abfc" },
  risk: { label: "Risk", glyph: "▲", color: "#fbbf24" },
  synthesis: { label: "Synthesis", glyph: "✦", color: "#34d399" },
};

const STATUS_STYLE = {
  running: { fg: "var(--accent)", label: "running" },
  ok: { fg: "var(--safe)", label: "ok" },
  degraded: { fg: "var(--caution)", label: "degraded" },
  failed: { fg: "var(--unsafe)", label: "failed" },
} as const;

function ToolCallRow({ c, lang }: { c: ToolCall; lang: Lang }) {
  const [open, setOpen] = useState(false);
  const statusColor =
    c.status === "ok" ? "var(--safe)"
    : c.status === "cache" ? "var(--derived)"
    : c.status === "fallback" ? "var(--snapshot)"
    : "var(--unsafe)";

  return (
    <div className="rounded-md" style={{ background: "rgba(255,255,255,0.025)", border: "1px solid var(--line-soft)" }}>
      <div className="flex items-start gap-2 px-2.5 py-2">
        <span className="mt-[3px] shrink-0 text-[10px]" style={{ color: statusColor }}>●</span>
        <div className="min-w-0 flex-1">
          <div className="mono text-[13.5px] leading-tight" style={{ color: "var(--text-dim)" }}>
            {c.tool}
          </div>
          {c.summary && (
            <div className="mt-0.5 text-[13.5px] leading-snug" style={{ color: "var(--text)" }}>
              {c.summary}
            </div>
          )}
          {c.url && (
            <a
              href={c.url}
              target="_blank"
              rel="noreferrer"
              className="mono mt-1 block truncate text-[11px] hover:underline"
              style={{ color: "var(--accent-dim)" }}
              title={c.url}
            >
              {c.url}
            </a>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className="mono text-[12.5px] tabular-nums" style={{ color: "var(--text-faint)" }}>
            {c.durationMs}ms
          </span>
          {c.raw !== undefined && (
            <button
              onClick={() => setOpen((v) => !v)}
              className="rounded px-1.5 py-0.5 text-[10.5px] transition-colors"
              style={{ border: "1px solid var(--line)", color: open ? "var(--accent)" : "var(--text-faint)" }}
            >
              {open ? t(lang, "trace.collapse") : t(lang, "trace.expand")}
            </button>
          )}
        </div>
      </div>
      {open && c.raw !== undefined && (
        <pre
          className="mono max-h-[26rem] overflow-auto px-3 py-2 text-[12.5px] leading-[1.55]"
          style={{ background: "#04101c", borderTop: "1px solid var(--line-soft)", color: "#a7cbe6" }}
        >
          {JSON.stringify(c.raw, null, 2)}
        </pre>
      )}
    </div>
  );
}

function StepCard({ step, lang }: { step: TraceStep; lang: Lang }) {
  const meta = AGENT_META[step.agent];
  const st = STATUS_STYLE[step.status];
  const r = step.result;

  return (
    <div
      className="card-in rounded-lg"
      style={{ background: "var(--panel-2)", border: `1px solid ${step.status === "running" ? "var(--accent-dim)" : "var(--line)"}` }}
    >
      <div className="flex items-start gap-3 px-3.5 py-3">
        <span
          className={`mt-[1px] flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[15px] ${step.status === "running" ? "pulse" : ""}`}
          style={{ background: "rgba(255,255,255,0.05)", color: meta.color }}
        >
          {meta.glyph}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[16px] font-semibold" style={{ color: meta.color }}>
              {meta.label}
            </span>
            <span className="text-[11px] uppercase tracking-wider" style={{ color: st.fg }}>
              {step.status === "running" ? t(lang, "trace.running") : st.label}
            </span>
            {r && (
              <span className="mono ml-auto text-[12.5px] tabular-nums" style={{ color: "var(--text-faint)" }}>
                {r.durationMs}ms
              </span>
            )}
          </div>
          <div className="mt-1 text-[14.5px] leading-snug" style={{ color: "var(--text-dim)" }}>
            {step.task}
          </div>
        </div>
      </div>

      {step.status === "running" && <div className="running-bar mx-3.5 h-[2px] rounded-full" />}

      {r && (
        <div className="space-y-2 px-3.5 pb-3">
          {r.error && (
            <div
              className="rounded-md px-2.5 py-1.5 text-[13.5px] leading-snug"
              style={{ background: "rgba(244,63,94,0.10)", border: "1px solid rgba(244,63,94,0.35)", color: "#fda4af" }}
            >
              {r.error}
            </div>
          )}
          {r.toolCalls.map((c, i) => (
            <ToolCallRow key={`${c.tool}-${i}`} c={c} lang={lang} />
          ))}
          {r.provenance.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {r.provenance.map((p, i) => (
                <ProvenanceChip key={i} p={p} lang={lang} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PlanCard({ plan, lang, durationMs }: { plan: QueryPlan; lang: Lang; durationMs: number }) {
  return (
    <div
      className="card-in rounded-lg"
      style={{ background: "rgba(167,139,250,0.07)", border: "1px solid rgba(167,139,250,0.35)" }}
    >
      <div className="flex items-baseline gap-2 px-3.5 pt-3">
        <span className="text-[16px] font-semibold" style={{ color: "#c4b5fd" }}>
          {t(lang, "trace.planner")}
        </span>
        <span
          className="rounded px-1.5 py-[1px] text-[10.5px] uppercase tracking-wider"
          style={{ border: "1px solid rgba(167,139,250,0.4)", color: "#c4b5fd" }}
        >
          {plan.plannerMode === "llm" ? "LLM" : "rule-based"}
        </span>
        <span className="mono ml-auto text-[12.5px] tabular-nums" style={{ color: "var(--text-faint)" }}>
          {durationMs}ms
        </span>
      </div>

      <div className="px-3.5 pb-1 pt-1.5 text-[14.5px] leading-snug" style={{ color: "var(--text)" }}>
        {plan.reasoning}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-1 px-3.5 pb-2 pt-1 text-[12.5px]">
        {[
          ["intent", plan.intent],
          ["language", plan.language],
          ["location", `${plan.location.name} (${plan.location.lat.toFixed(3)}, ${plan.location.lon.toFixed(3)})`],
          ["resolved by", plan.location.resolvedBy],
          ["window", plan.timeWindow.label],
          ["window ISO", `${plan.timeWindow.startIST} → ${plan.timeWindow.endIST}`],
          ...(plan.destination ? ([["destination", plan.destination.name]] as [string, string][]) : []),
        ].map(([k, v]) => (
          <div key={k} className="flex min-w-0 gap-1.5">
            <span className="shrink-0" style={{ color: "var(--text-faint)" }}>{k}:</span>
            <span className="mono truncate" style={{ color: "var(--text-dim)" }} title={String(v)}>{String(v)}</span>
          </div>
        ))}
      </div>

      <div className="px-3.5 pb-3">
        <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
          {t(lang, "trace.plan")} · {plan.steps.length} {t(lang, "trace.steps")}
        </div>
        <ol className="space-y-1">
          {plan.steps.map((s, i) => (
            <li key={`${s.agent}-${i}`} className="flex items-start gap-2 text-[13.5px]">
              <span className="mono mt-[1px] shrink-0" style={{ color: "var(--text-faint)" }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="shrink-0 font-semibold" style={{ color: AGENT_META[s.agent].color }}>
                {AGENT_META[s.agent].label}
              </span>
              <span className="min-w-0" style={{ color: "var(--text-dim)" }}>
                {s.task}
                {s.dependsOn.length > 0 && (
                  <span className="mono" style={{ color: "var(--text-faint)" }}>
                    {" "}← {s.dependsOn.join(", ")}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

export default function TracePane({
  plan,
  planDurationMs,
  steps,
  evidence,
  confidence,
  notices,
  totalMs,
  running,
  llmAvailable,
  lang = "en",
}: {
  plan: QueryPlan | null;
  planDurationMs: number;
  steps: TraceStep[];
  evidence: EvidenceItem[];
  confidence: number | null;
  notices: Array<{ level: string; message: string }>;
  totalMs: number | null;
  running: boolean;
  llmAvailable: boolean;
  lang?: Lang;
}) {
  const empty = !plan && steps.length === 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5" style={{ borderColor: "var(--line)" }}>
        <h2 className="text-[13px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
          {t(lang, "panes.trace")}
        </h2>
        {running && (
          <span className="flex items-center gap-1.5 text-[11px]" style={{ color: "var(--accent)" }}>
            <span className="h-1.5 w-1.5 rounded-full pulse" style={{ background: "var(--accent)" }} />
            {t(lang, "trace.running")}
          </span>
        )}
        {!llmAvailable && (
          <span
            className="ml-auto rounded px-1.5 py-[2px] text-[10.5px]"
            style={{ border: "1px solid var(--line)", color: "var(--text-faint)" }}
            title={t(lang, "trace.noLlm")}
          >
            {t(lang, "trace.noLlm")}
          </span>
        )}
        {totalMs !== null && !running && (
          <span className="mono ml-auto text-[12.5px] tabular-nums" style={{ color: "var(--text-faint)" }}>
            {t(lang, "trace.done")} · {totalMs}ms
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto px-3.5 py-3">
        {empty && (
          <div className="flex h-full items-center justify-center px-6 text-center text-[15px]" style={{ color: "var(--text-faint)" }}>
            {t(lang, "trace.waiting")}
          </div>
        )}

        {notices.map((n, i) => (
          <div
            key={i}
            className="card-in rounded-md px-3 py-2 text-[13.5px] leading-snug"
            style={{ background: "rgba(245,158,11,0.10)", border: "1px solid rgba(245,158,11,0.35)", color: "#fcd34d" }}
          >
            {n.message}
          </div>
        ))}

        {plan && <PlanCard plan={plan} lang={lang} durationMs={planDurationMs} />}

        {steps
          .filter((s) => s.agent !== "planner")
          .map((s) => (
            <StepCard key={s.key} step={s} lang={lang} />
          ))}

        {evidence.length > 0 && (
          <div className="card-in rounded-lg" style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
            <div className="flex items-baseline gap-2 border-b px-3.5 py-2.5" style={{ borderColor: "var(--line-soft)" }}>
              <span className="text-[13px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
                {t(lang, "trace.evidence")}
              </span>
              {confidence !== null && (
                <span
                  className="ml-auto rounded-md px-2 py-[3px] text-[12px] font-semibold tabular-nums"
                  style={{
                    background: confidence >= 0.8 ? "rgba(16,185,129,0.16)" : confidence >= 0.5 ? "rgba(245,158,11,0.16)" : "rgba(244,63,94,0.16)",
                    color: confidence >= 0.8 ? "#34d399" : confidence >= 0.5 ? "#fbbf24" : "#fda4af",
                  }}
                >
                  {t(lang, "trace.confidence")} {Math.round(confidence * 100)}%
                </span>
              )}
            </div>
            <ul className="divide-y" style={{ borderColor: "var(--line-soft)" }}>
              {evidence.map((e, i) => (
                <li key={i} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 px-3.5 py-2">
                  <span className="text-[14px]" style={{ color: "var(--text-dim)" }}>{e.fact}</span>
                  <span className="mono text-[14px] font-semibold" style={{ color: "var(--text)" }}>{e.value}</span>
                  <span
                    className="rounded px-1.5 py-[1px] text-[10.5px]"
                    style={{ border: `1px solid ${AGENT_META[e.agent].color}55`, color: AGENT_META[e.agent].color }}
                  >
                    {AGENT_META[e.agent].label}
                  </span>
                  <span className="ml-auto">
                    <ProvenanceChip p={e.provenance} lang={lang} showSource={false} />
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
