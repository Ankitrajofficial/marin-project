"use client";

import { useEffect, useRef, useState } from "react";
import type { AnswerPayload, Lang } from "@/lib/types";
import { t, tList } from "@/lib/i18n";
import { LANGS, speechCode } from "@/lib/lang";
import { SCENARIOS } from "@/lib/scenarios";
import { ProvenanceRow } from "./ProvenanceChip";
import type { Provenance } from "@/lib/types";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  lang: Lang;
  answer?: AnswerPayload;
  provenance?: Provenance[];
  pending?: boolean;
}

// Minimal typings for the Web Speech API, which TypeScript's DOM lib does not ship.
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  onresult: ((e: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
}

export default function ConversationPane({
  messages,
  onAsk,
  onScenario,
  onReset,
  lang,
  setLang,
  busy,
}: {
  messages: ChatMessage[];
  onAsk: (q: string) => void;
  onScenario: (id: string) => void;
  onReset: () => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  busy: boolean;
}) {
  const [input, setInput] = useState("");
  const [listening, setListening] = useState(false);
  const [micSupported, setMicSupported] = useState(true);
  const recRef = useRef<SpeechRecognitionLike | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const w = window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike; webkitSpeechRecognition?: new () => SpeechRecognitionLike };
    setMicSupported(Boolean(w.SpeechRecognition || w.webkitSpeechRecognition));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const submit = (q: string) => {
    const text = q.trim();
    if (!text || busy) return;
    setInput("");
    onAsk(text);
  };

  /** Voice input via the browser Web Speech API - free, no service, no key. */
  const toggleMic = () => {
    const w = window as unknown as { SpeechRecognition?: new () => SpeechRecognitionLike; webkitSpeechRecognition?: new () => SpeechRecognitionLike };
    const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!Ctor) return setMicSupported(false);

    if (listening) {
      recRef.current?.stop();
      setListening(false);
      return;
    }
    try {
      const rec = new Ctor();
      rec.lang = speechCode(lang);
      rec.continuous = false;
      rec.interimResults = false;
      rec.onresult = (e) => {
        const transcript = e.results[0]?.[0]?.transcript ?? "";
        if (transcript) submit(transcript);
      };
      rec.onerror = () => setListening(false);
      rec.onend = () => setListening(false);
      recRef.current = rec;
      rec.start();
      setListening(true);
    } catch {
      setMicSupported(false);
      setListening(false);
    }
  };

  const speak = (text: string, l: Lang) => {
    if (!("speechSynthesis" in window)) return;
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = speechCode(l);
    window.speechSynthesis.speak(u);
  };

  const chipItems = tList(lang, "chips.items");

  return (
    <div className="flex h-full flex-col">
      {/* header */}
      <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5" style={{ borderColor: "var(--line)" }}>
        <h2 className="text-[13px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
          {t(lang, "panes.conversation")}
        </h2>
        <select
          value={lang}
          onChange={(e) => setLang(e.target.value as Lang)}
          className="ml-auto rounded-md px-2 py-1 text-[12px] outline-none"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
          aria-label={t(lang, "input.language")}
        >
          {LANGS.map((l) => (
            <option key={l.code} value={l.code} style={{ background: "#0b1524" }}>
              {l.native}
            </option>
          ))}
        </select>
        <button
          onClick={onReset}
          className="rounded-md px-2 py-1 text-[11.5px] transition-colors hover:bg-white/5"
          style={{ border: "1px solid var(--line)", color: "var(--text-faint)" }}
          title="Ctrl/Cmd + Shift + K"
        >
          {t(lang, "input.reset")}
        </button>
      </div>

      {/* scenarios */}
      <div className="shrink-0 border-b px-3 py-2.5" style={{ borderColor: "var(--line)" }}>
        <div className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
          {t(lang, "scenarios.title")}
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          {SCENARIOS.map((s, i) => (
            <button
              key={s.id}
              onClick={() => !busy && onScenario(s.id)}
              disabled={busy}
              className="rounded-md px-2 py-1.5 text-left transition-colors hover:bg-white/5 disabled:opacity-40"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}
            >
              <div className="flex items-baseline gap-1.5">
                <span className="mono text-[10px]" style={{ color: "var(--accent-dim)" }}>{i + 1}</span>
                <span className="truncate text-[12px] font-semibold" style={{ color: "var(--text)" }}>{s.title}</span>
              </div>
              <div className="truncate text-[10.5px]" style={{ color: "var(--text-faint)" }}>{s.subtitle}</div>
            </button>
          ))}
        </div>
      </div>

      {/* messages */}
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-3.5 py-3">
        {messages.length === 0 && (
          <div className="space-y-2 pt-1">
            <div className="text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
              {t(lang, "chips.title")}
            </div>
            {chipItems.map((c, i) => (
              <button
                key={i}
                onClick={() => submit(c)}
                className="block w-full rounded-lg px-3 py-2 text-left text-[13.5px] leading-snug transition-colors hover:bg-white/5"
                style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text-dim)" }}
              >
                {c}
              </button>
            ))}
          </div>
        )}

        {messages.map((m) => (
          <div key={m.id} className={m.role === "user" ? "flex justify-end" : ""}>
            <div
              className={`card-in max-w-[95%] rounded-xl px-3.5 py-2.5 ${m.role === "user" ? "" : "w-full"}`}
              style={
                m.role === "user"
                  ? { background: "rgba(34,211,238,0.13)", border: "1px solid rgba(34,211,238,0.34)" }
                  : { background: "var(--panel-2)", border: "1px solid var(--line)" }
              }
            >
              {m.role === "assistant" && (
                <div className="mb-1.5 flex items-center gap-2">
                  <span className="text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--accent)" }}>
                    ORCA
                  </span>
                  {m.answer && (
                    <span
                      className="rounded px-1.5 py-[1px] text-[10px] uppercase tracking-wide"
                      style={{ border: "1px solid var(--line)", color: "var(--text-faint)" }}
                    >
                      {m.answer.mode === "llm" ? "LLM synthesis" : "template synthesis"}
                    </span>
                  )}
                  {m.answer?.band && (
                    <span
                      className="rounded px-1.5 py-[1px] text-[10px] font-bold"
                      style={{
                        background: m.answer.band === "UNSAFE" ? "var(--unsafe)" : m.answer.band === "CAUTION" ? "var(--caution)" : m.answer.band === "UNKNOWN" ? "#94a3b8" : "var(--safe)",
                        color: "#04121c",
                      }}
                    >
                      {t(m.lang, `risk.band.${m.answer.band}`)}
                    </span>
                  )}
                  {!m.pending && (
                    <button
                      onClick={() => speak(m.text, m.lang)}
                      className="ml-auto rounded px-1.5 py-[2px] text-[10.5px] transition-colors hover:bg-white/5"
                      style={{ border: "1px solid var(--line)", color: "var(--text-faint)" }}
                      title={t(lang, "input.speak")}
                    >
                      ▶ {t(lang, "input.speak")}
                    </button>
                  )}
                </div>
              )}
              <div className="whitespace-pre-wrap text-[15px] leading-relaxed" style={{ color: m.pending ? "var(--text-faint)" : "var(--text)" }}>
                {m.text}
              </div>
              {m.provenance && m.provenance.length > 0 && (
                <div className="mt-2 border-t pt-2" style={{ borderColor: "var(--line-soft)" }}>
                  <ProvenanceRow items={m.provenance} lang={m.lang} />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* composer */}
      <div className="shrink-0 border-t p-3" style={{ borderColor: "var(--line)" }}>
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit(input);
              }
            }}
            rows={2}
            placeholder={t(lang, "input.placeholder")}
            className="min-h-[54px] flex-1 resize-none rounded-lg px-3 py-2 text-[14px] outline-none"
            style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
          />
          <button
            onClick={toggleMic}
            disabled={!micSupported}
            title={micSupported ? t(lang, "input.mic") : t(lang, "input.micUnsupported")}
            className={`flex h-[54px] w-[46px] items-center justify-center rounded-lg text-[17px] transition-colors disabled:opacity-30 ${listening ? "pulse" : ""}`}
            style={{
              background: listening ? "rgba(244,63,94,0.2)" : "var(--panel-2)",
              border: `1px solid ${listening ? "var(--unsafe)" : "var(--line)"}`,
              color: listening ? "#fda4af" : "var(--text-dim)",
            }}
          >
            {listening ? "■" : "🎙"}
          </button>
          <button
            onClick={() => submit(input)}
            disabled={busy || !input.trim()}
            className="h-[54px] rounded-lg px-4 text-[14px] font-semibold transition-opacity disabled:opacity-35"
            style={{ background: "var(--accent)", color: "#04121c" }}
          >
            {busy ? "…" : t(lang, "input.send")}
          </button>
        </div>
        {listening && (
          <div className="mt-1.5 text-[11.5px]" style={{ color: "#fda4af" }}>
            {t(lang, "input.listening")}
          </div>
        )}
      </div>
    </div>
  );
}
