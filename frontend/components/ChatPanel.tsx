"use client";

import { useEffect, useRef, useState } from "react";
import { streamChat } from "@/lib/api";
import type { ChatResponse } from "@/lib/types";

interface Turn {
  role: "user" | "assistant";
  text: string;
  meta?: ChatResponse;
  error?: boolean;
  streaming?: boolean;
}

const EXAMPLES = [
  "Is it safe to go to sea tomorrow morning near Nagapattinam?",
  "How close is Katchatheevu to the boundary?",
  "What are the conditions at Kochi right now?",
];

const age = (m: number | null, lower: boolean) => {
  if (m === null) return "age unknown";
  const t = m < 90 ? `${m.toFixed(0)} min` : `${(m / 60).toFixed(1)} h`;
  return lower ? `at least ${t} old` : `${t} old`;
};

export default function ChatPanel({
  onHighlight,
}: {
  onHighlight: (cells: string[], zones: string[]) => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [phases, setPhases] = useState<string[]>([]);
  const [session, setSession] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns, busy]);

  async function submit(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    // The assistant turn is created empty and filled as the stream arrives.
    setTurns((t) => [...t, { role: "user", text: q },
                     { role: "assistant", text: "", streaming: true }]);
    setBusy(true);
    setPhases([]);

    const patchLast = (fn: (t: Turn) => Turn) =>
      setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

    try {
      await streamChat(q, session, {
        onOpen: setSession,
        // The deterministic work, narrated. This is the part of the answer
        // that is actually trustworthy, so it is shown rather than hidden
        // behind a spinner.
        onPhase: (label, phase) =>
          setPhases((p) => (phase === "tool_done" ? p : [...p, label])),
        onPlan: (_intent, labels) =>
          setPhases((p) => [...p, ...labels.filter((l) => !p.includes(l))]),
        onToken: (chunk) => patchLast((t) => ({ ...t, text: t.text + chunk })),
        onDone: (payload) => {
          patchLast((t) => ({ ...t, text: payload.answer, meta: payload,
                              streaming: false }));
          onHighlight(payload.highlights.cells, payload.highlights.zones);
          setPhases([]);
        },
        // Never fabricate an answer on failure. Say what went wrong.
        onError: (message) =>
          patchLast((t) => ({ ...t, text: message, error: true,
                              streaming: false })),
      });
    } catch (e) {
      patchLast((t) => ({ ...t, text: (e as Error).message, error: true,
                          streaming: false }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="chat">
      <div className="chat-head">
        <b>Ask ORCA</b>
        <span className="hint"> · every number comes from a computed result</span>
      </div>

      <div className="chat-log">
        {turns.length === 0 && (
          <div className="hint">
            <p style={{ marginTop: 0 }}>Try:</p>
            {EXAMPLES.map((e) => (
              <button key={e} className="chip" onClick={() => submit(e)}>{e}</button>
            ))}
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className={`bubble ${t.role}${t.error ? " err-bubble" : ""}`}>
            <div style={{ whiteSpace: "pre-wrap" }}>
              {t.text}
              {t.streaming && <span className="caret" />}
            </div>

            {t.meta && (
              <>
                <div className="flags">
                  {t.meta.simulated && <span className="badge sim">SIMULATED</span>}
                  {t.meta.advisory_only && (
                    <span className="badge nb">ADVISORY BOUNDARIES</span>
                  )}
                  {t.meta.fell_back && (
                    <span className="badge nb" title="The generated wording was rejected by the number guard twice; these are the computed results verbatim.">
                      VERBATIM RESULTS
                    </span>
                  )}
                </div>

                {t.meta.sources.length > 0 && (
                  <div className="srcs">
                    {t.meta.sources.map((s) => (
                      <div key={s.source_id}>
                        <b>{s.source_id}</b> — {s.variables.join(", ")} ·{" "}
                        {age(s.age_minutes, s.age_is_lower_bound)}
                        {s.simulated && " · SIMULATED"}
                      </div>
                    ))}
                  </div>
                )}

                {/* Collapsed by default: available for anyone who wants to check
                    a number, out of the way for anyone who doesn't. */}
                <details className="trace">
                  <summary>reasoning trace ({t.meta.trace.length} steps)</summary>
                  {t.meta.findings && (
                    <>
                      <div className="trace-h">computed findings</div>
                      <pre>{t.meta.findings}</pre>
                    </>
                  )}
                  {t.meta.trace.map((s, j) => (
                    <div key={j}>
                      <div className="trace-h">
                        {String(s.node)}
                        {s.tool ? ` → ${String(s.tool)}` : ""}
                        {s.duration_ms ? ` · ${String(s.duration_ms)} ms` : ""}
                      </div>
                      <pre>{JSON.stringify(s.input ?? {}, null, 1)}</pre>
                      <pre>{JSON.stringify(s.output ?? {}, null, 1).slice(0, 1600)}</pre>
                    </div>
                  ))}
                </details>
              </>
            )}
          </div>
        ))}

        {/* Live account of the deterministic work while it runs. */}
        {busy && phases.length > 0 && (
          <div className="bubble assistant phases">
            {phases.map((p, i) => (
              <div key={i} className={i === phases.length - 1 ? "phase-now" : "phase-done"}>
                {i === phases.length - 1 ? "▸ " : "✓ "}{p}
                {i === phases.length - 1 ? "…" : ""}
              </div>
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form className="chat-in" onSubmit={(e) => { e.preventDefault(); submit(input); }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Is it safe to go out tomorrow morning near…"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>Ask</button>
      </form>
    </aside>
  );
}
