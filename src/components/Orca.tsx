"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ConversationPane, { type ChatMessage } from "./ConversationPane";
import TracePane, { type TraceStep } from "./TracePane";
import MapPane from "./MapPane";
import RiskGauge from "./RiskGauge";
import AlertsPanel from "./AlertsPanel";
import { t } from "@/lib/i18n";
import { getScenario } from "@/lib/scenarios";
import type { AnswerPayload, Lang, MapPayload, QueryPlan, RiskData, StreamEvent } from "@/lib/types";

interface RunState {
  plan: QueryPlan | null;
  planDurationMs: number;
  steps: TraceStep[];
  risk: RiskData | null;
  map: MapPayload | null;
  answer: AnswerPayload | null;
  notices: Array<{ level: string; message: string }>;
  totalMs: number | null;
  llmAvailable: boolean;
}

const EMPTY: RunState = {
  plan: null, planDurationMs: 0, steps: [], risk: null, map: null,
  answer: null, notices: [], totalMs: null, llmAvailable: false,
};

export default function Orca() {
  const [lang, setLang] = useState<Lang>("en");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [run, setRun] = useState<RunState>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [showAlerts, setShowAlerts] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const ask = useCallback(
    async (query: string, scenarioId?: string) => {
      // Rapid repeated clicking must not stack runs: cancel any in-flight request first.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const scenario = getScenario(scenarioId);
      const userMsgId = `u_${Date.now()}`;
      setBusy(true);
      setRun({ ...EMPTY, steps: [] });
      setMessages((m) => [
        ...m,
        { id: userMsgId, role: "user", text: query, lang },
        { id: `a_${Date.now()}`, role: "assistant", text: "…", lang, pending: true },
      ]);

      try {
        const res = await fetch("/api/query", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ query, language: lang, scenarioId: scenario?.id ?? null }),
          signal: controller.signal,
        });
        if (!res.body) throw new Error("No response stream from the server");

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const chunks = buffer.split("\n\n");
          buffer = chunks.pop() ?? "";

          for (const chunk of chunks) {
            const line = chunk.split("\n").find((l) => l.startsWith("data: "));
            if (!line) continue;
            let ev: StreamEvent;
            try {
              ev = JSON.parse(line.slice(6)) as StreamEvent;
            } catch {
              continue;
            }

            setRun((prev) => {
              const next = { ...prev };
              switch (ev.type) {
                case "meta":
                  next.llmAvailable = ev.llmAvailable;
                  break;
                case "plan":
                  next.plan = ev.plan;
                  next.planDurationMs = ev.durationMs;
                  break;
                case "step_start":
                  next.steps = [
                    ...prev.steps,
                    { key: `${ev.agent}-${ev.index}`, agent: ev.agent, task: ev.task, index: ev.index, status: "running" },
                  ];
                  break;
                case "step_end": {
                  const r = ev.result;
                  const status: TraceStep["status"] = !r.ok ? "failed" : r.degraded ? "degraded" : "ok";
                  const i = prev.steps.findIndex((s) => s.agent === r.agent && s.status === "running");
                  const steps = [...prev.steps];
                  if (i >= 0) steps[i] = { ...steps[i], status, result: r, task: r.task };
                  else steps.push({ key: `${r.agent}-x`, agent: r.agent, task: r.task, index: steps.length, status, result: r });
                  next.steps = steps;
                  break;
                }
                case "risk":
                  next.risk = ev.risk;
                  break;
                case "map":
                  next.map = ev.map;
                  break;
                case "notice":
                  next.notices = [...prev.notices, { level: ev.level, message: ev.message }];
                  break;
                case "answer":
                  next.answer = ev.answer;
                  break;
                case "done":
                  next.totalMs = ev.totalMs;
                  break;
                case "error":
                  next.notices = [...prev.notices, { level: "warn", message: ev.message }];
                  break;
              }
              return next;
            });

            if (ev.type === "answer") {
              const a = ev.answer;
              setMessages((m) => {
                const copy = [...m];
                const idx = copy.map((x) => x.pending).lastIndexOf(true);
                if (idx >= 0) {
                  copy[idx] = {
                    ...copy[idx],
                    text: a.text,
                    lang: a.language,
                    answer: a,
                    provenance: a.evidence.map((e) => e.provenance),
                    pending: false,
                  };
                }
                return copy;
              });
            }
          }
        }
      } catch (err) {
        if ((err as Error)?.name === "AbortError") return;
        const message = err instanceof Error ? err.message : String(err);
        setMessages((m) => {
          const copy = [...m];
          const idx = copy.map((x) => x.pending).lastIndexOf(true);
          if (idx >= 0) {
            copy[idx] = {
              ...copy[idx],
              pending: false,
              text: `The request could not be completed: ${message}. The pipeline degrades rather than hiding the failure — check the trace pane for which agent stopped.`,
            };
          }
          return copy;
        });
        setRun((p) => ({ ...p, notices: [...p.notices, { level: "warn", message }] }));
      } finally {
        if (abortRef.current === controller) {
          setBusy(false);
          abortRef.current = null;
        }
      }
    },
    [lang],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setMessages([]);
    setRun(EMPTY);
    setBusy(false);
    void fetch("/api/reset", { method: "POST" }).catch(() => {});
  }, []);

  /** Hidden demo reset: Ctrl/Cmd + Shift + K clears everything between judging panels. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        reset();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [reset]);

  /**
   * Cold-start prefetch. Warms the Next route, the layer payload and the two upstream APIs
   * for the default demo location so the first scenario a judge clicks is not the slow one.
   */
  useEffect(() => {
    const id = setTimeout(() => {
      void fetch("/api/layers").catch(() => {});
      void fetch("/api/alerts?harbour=chennai").catch(() => {});
    }, 400);
    return () => clearTimeout(id);
  }, []);

  const evidence = run.answer?.evidence ?? [];

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden">
      {/* masthead */}
      <header
        className="flex shrink-0 items-center gap-3 border-b px-4 py-2"
        style={{ borderColor: "var(--line)", background: "var(--panel)" }}
      >
        <div className="flex items-baseline gap-2.5">
          <span className="text-[19px] font-bold tracking-tight" style={{ color: "var(--accent)" }}>
            {t(lang, "app.title")}
          </span>
          <span className="hidden text-[13px] sm:inline" style={{ color: "var(--text-dim)" }}>
            {t(lang, "app.tagline")}
          </span>
        </div>
        <span
          className="hidden rounded px-2 py-[2px] text-[10.5px] md:inline"
          style={{ border: "1px solid var(--line)", color: "var(--text-faint)" }}
        >
          {t(lang, "app.badge")}
        </span>

        <div className="ml-auto flex items-center gap-2">
          <span
            className="hidden rounded px-2 py-[3px] text-[10.5px] lg:inline"
            style={{ background: "rgba(16,185,129,0.12)", border: "1px solid rgba(16,185,129,0.4)", color: "#34d399" }}
          >
            LIVE: wave · swell · wind · SST
          </span>
          <span
            className="hidden rounded px-2 py-[3px] text-[10.5px] lg:inline"
            style={{ background: "rgba(245,158,11,0.12)", border: "1px solid rgba(245,158,11,0.4)", color: "#fbbf24" }}
          >
            CACHED: PFZ · boundaries · MPA
          </span>
          <button
            onClick={() => setShowAlerts((v) => !v)}
            className="rounded-md px-2.5 py-1 text-[12px] transition-colors hover:bg-white/5"
            style={{ border: "1px solid var(--line)", color: showAlerts ? "var(--accent)" : "var(--text-dim)" }}
          >
            {t(lang, "alerts.title")}
          </button>
        </div>
      </header>

      {/* three panes */}
      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(320px,25fr)_minmax(380px,38fr)_minmax(380px,37fr)]">
        <section className="min-h-0 border-b lg:border-b-0 lg:border-r" style={{ borderColor: "var(--line)", background: "var(--panel)" }}>
          <ConversationPane
            messages={messages}
            onAsk={(q) => void ask(q)}
            onScenario={(id) => {
              const s = getScenario(id);
              if (s) void ask(s.query, s.id);
            }}
            onReset={reset}
            lang={lang}
            setLang={setLang}
            busy={busy}
          />
        </section>

        <section className="min-h-0 border-b lg:border-b-0 lg:border-r" style={{ borderColor: "var(--line)", background: "var(--bg)" }}>
          <TracePane
            plan={run.plan}
            planDurationMs={run.planDurationMs}
            steps={run.steps}
            evidence={evidence}
            confidence={run.answer?.confidence ?? null}
            notices={run.notices}
            totalMs={run.totalMs}
            running={busy}
            llmAvailable={run.llmAvailable}
            lang={lang}
          />
        </section>

        <section className="flex min-h-0 flex-col" style={{ background: "var(--panel)" }}>
          <div className="relative min-h-[300px] flex-[1.15]">
            <MapPane payload={run.map} lang={lang} />
          </div>
          <div
            className="min-h-0 flex-1 overflow-y-auto border-t"
            style={{ borderColor: "var(--line)", background: "var(--panel)" }}
          >
            {showAlerts ? <AlertsPanel lang={lang} /> : <RiskGauge risk={run.risk} lang={lang} />}
          </div>
        </section>
      </div>
    </div>
  );
}
