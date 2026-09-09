"use client";

import { useCallback, useEffect, useState } from "react";
import type { Lang, Provenance, RiskData } from "@/lib/types";
import { t } from "@/lib/i18n";
import { HARBOURS } from "@/lib/layers";
import { ProvenanceRow } from "./ProvenanceChip";

interface AlertsResponse {
  location: { id: string; name: string; state: string };
  evaluatedAt: string;
  window: string;
  risk: RiskData;
  alerts: Array<{ id: string; severity: string; label: string; measured: string; threshold: string; source: string }>;
  provenance: Provenance[];
  notificationPreview: {
    channelNote: string;
    sms: string;
    smsLength: number;
    ivrScript: string;
    push: { title: string; body: string };
  };
}

/**
 * Proactive alerts for a saved location, plus a faithful preview of the notification that
 * production would push over SMS / IVR. Nothing is actually sent - no provider is wired in.
 */
export default function AlertsPanel({ lang = "en" }: { lang?: Lang }) {
  const [harbour, setHarbour] = useState("kanyakumari");
  const [data, setData] = useState<AlertsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    try {
      const r = await fetch(`/api/alerts?harbour=${encodeURIComponent(id)}`);
      setData(await r.json());
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(harbour);
  }, [harbour, load]);

  const band = data?.risk.band ?? "SAFE";
  const bandColor =
    band === "UNSAFE" ? "var(--unsafe)" : band === "CAUTION" ? "var(--caution)" : band === "UNKNOWN" ? "#94a3b8" : "var(--safe)";

  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-2">
        <h3 className="text-[12px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-dim)" }}>
          {t(lang, "alerts.title")}
        </h3>
        <select
          value={harbour}
          onChange={(e) => setHarbour(e.target.value)}
          className="ml-auto max-w-[160px] truncate rounded-md px-2 py-1 text-[11.5px] outline-none"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--text)" }}
          aria-label={t(lang, "alerts.saved")}
        >
          {HARBOURS.map((h) => (
            <option key={h.id} value={h.id} style={{ background: "#0b1524" }}>
              {h.name}
            </option>
          ))}
        </select>
        <button
          onClick={() => void load(harbour)}
          className="rounded-md px-2 py-1 text-[11px] transition-colors hover:bg-white/5"
          style={{ border: "1px solid var(--line)", color: "var(--text-faint)" }}
        >
          {loading ? "…" : t(lang, "alerts.evaluate")}
        </button>
      </div>

      {data && (
        <>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="rounded px-2 py-[2px] text-[11px] font-bold" style={{ background: bandColor, color: "#04121c" }}>
              {t(lang, `risk.band.${band}`)}
            </span>
            <span className="text-[12px]" style={{ color: "var(--text-dim)" }}>
              {data.location.name} · {data.window}
            </span>
            <span className="mono ml-auto text-[10.5px]" style={{ color: "var(--text-faint)" }}>
              {data.evaluatedAt}
            </span>
          </div>

          {data.alerts.length === 0 ? (
            <div className="mt-2 text-[12.5px]" style={{ color: "var(--text-faint)" }}>
              {t(lang, "alerts.none")}
            </div>
          ) : (
            <ul className="mt-2 space-y-1">
              {data.alerts.map((a) => (
                <li
                  key={a.id}
                  className="rounded-md px-2.5 py-1.5 text-[12.5px]"
                  style={{
                    background: "rgba(255,255,255,0.03)",
                    borderLeft: `3px solid ${a.severity === "danger" ? "var(--unsafe)" : "var(--caution)"}`,
                    color: "var(--text-dim)",
                  }}
                >
                  {a.label}{" "}
                  <span className="mono" style={{ color: "var(--text)" }}>
                    ({a.measured} vs {a.threshold})
                  </span>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-3 rounded-lg p-2.5" style={{ background: "rgba(255,255,255,0.03)", border: "1px dashed var(--line)" }}>
            <div className="text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
              {t(lang, "alerts.preview")}
            </div>

            <div className="mt-1.5 space-y-1.5">
              <div className="rounded-md px-2.5 py-1.5" style={{ background: "#0a1626", border: "1px solid var(--line)" }}>
                <div className="mono text-[10px]" style={{ color: "var(--text-faint)" }}>
                  SMS · {data.notificationPreview.smsLength}/160 chars
                </div>
                <div className="mono mt-0.5 text-[12px] leading-snug" style={{ color: "var(--text)" }}>
                  {data.notificationPreview.sms}
                </div>
              </div>
              <div className="rounded-md px-2.5 py-1.5" style={{ background: "#0a1626", border: "1px solid var(--line)" }}>
                <div className="mono text-[10px]" style={{ color: "var(--text-faint)" }}>IVR script</div>
                <div className="mt-0.5 text-[12px] leading-snug" style={{ color: "var(--text-dim)" }}>
                  {data.notificationPreview.ivrScript}
                </div>
              </div>
              <div className="rounded-md px-2.5 py-1.5" style={{ background: "#0a1626", border: "1px solid var(--line)" }}>
                <div className="mono text-[10px]" style={{ color: "var(--text-faint)" }}>Push</div>
                <div className="mt-0.5 text-[12px] font-semibold" style={{ color: "var(--text)" }}>
                  {data.notificationPreview.push.title}
                </div>
                <div className="text-[12px]" style={{ color: "var(--text-dim)" }}>
                  {data.notificationPreview.push.body}
                </div>
              </div>
            </div>

            <div className="mt-1.5 text-[10.5px] italic leading-snug" style={{ color: "var(--text-faint)" }}>
              {t(lang, "alerts.channel")}
            </div>
          </div>

          <div className="mt-2">
            <ProvenanceRow items={data.provenance} lang={lang} />
          </div>
        </>
      )}
    </div>
  );
}
