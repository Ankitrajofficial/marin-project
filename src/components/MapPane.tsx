"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import type { Lang, MapPayload } from "@/lib/types";
import { t } from "@/lib/i18n";

const LeafletMap = dynamic(() => import("./LeafletMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-full items-center justify-center text-[13px]" style={{ color: "var(--text-faint)" }}>
      Loading map…
    </div>
  ),
});

const LEGEND: Array<{ id: string; color: string; dash?: boolean; fill?: boolean }> = [
  { id: "pfz", color: "#10b981", fill: true },
  { id: "imbl", color: "#f43f5e", dash: true },
  { id: "mpa", color: "#a78bfa", fill: true },
  { id: "harbours", color: "#64748b", fill: true },
  { id: "eez", color: "#0e7490", dash: true },
  { id: "route", color: "#22d3ee" },
];

export default function MapPane({ payload, lang = "en" }: { payload: MapPayload | null; lang?: Lang }) {
  const [visible, setVisible] = useState<Record<string, boolean>>({
    pfz: true, imbl: true, mpa: true, harbours: true, coastline: true, eez: false, route: true,
  });

  const toggle = (id: string) => setVisible((v) => ({ ...v, [id]: !v[id] }));

  return (
    <div className="relative h-full w-full">
      <LeafletMap payload={payload} visible={visible} />

      {/* Layer toggles + legend */}
      <div
        className="absolute right-3 top-3 z-[500] w-[186px] rounded-lg p-2.5"
        style={{ background: "rgba(11,21,36,0.94)", border: "1px solid var(--line)", backdropFilter: "blur(6px)" }}
      >
        <div className="mb-1.5 text-[10.5px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-faint)" }}>
          {t(lang, "map.legend")}
        </div>
        <div className="space-y-[3px]">
          {LEGEND.map((l) => (
            <button
              key={l.id}
              onClick={() => toggle(l.id)}
              className="flex w-full items-center gap-2 rounded px-1 py-[3px] text-left text-[12px] transition-colors hover:bg-white/5"
              style={{ color: visible[l.id] ? "var(--text)" : "var(--text-faint)" }}
            >
              <span
                className="h-[3px] w-4 shrink-0 rounded-full"
                style={{
                  background: visible[l.id] ? l.color : "var(--line)",
                  opacity: l.dash ? 0.85 : 1,
                  border: l.dash ? `1px dashed ${visible[l.id] ? l.color : "var(--line)"}` : undefined,
                }}
              />
              <span className="truncate">{t(lang, `map.layers.${l.id}`)}</span>
              <span className="ml-auto text-[10px]">{visible[l.id] ? "●" : "○"}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Boundary proximity banner */}
      {payload?.boundaryWarning && (
        <div
          className="absolute left-3 top-3 z-[500] max-w-[300px] rounded-lg px-3 py-2"
          style={{
            background: "rgba(244,63,94,0.16)",
            border: "1px solid rgba(244,63,94,0.55)",
            backdropFilter: "blur(6px)",
          }}
        >
          <div className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: "#fda4af" }}>
            {t(lang, "map.boundaryWarning")} · {payload.boundaryWarning.severity}
          </div>
          <div className="mt-0.5 text-[13px] leading-snug" style={{ color: "var(--text)" }}>
            {payload.boundaryWarning.distanceNm} nm — {payload.boundaryWarning.name}
          </div>
        </div>
      )}

      {/* Standing honesty note about the cached layers */}
      <div
        className="absolute bottom-[22px] left-3 z-[500] max-w-[330px] rounded-md px-2.5 py-1.5 text-[10.5px] leading-snug"
        style={{ background: "rgba(11,21,36,0.92)", border: "1px solid var(--line)", color: "var(--text-faint)" }}
      >
        PFZ, boundary and protected-area layers are <span style={{ color: "var(--cached)" }}>CACHED</span> demo
        layers. PFZ polygons are an ORCA derivation, not an INCOIS advisory. Wave, swell, wind and SST are{" "}
        <span style={{ color: "var(--live)" }}>LIVE</span>.
      </div>
    </div>
  );
}
