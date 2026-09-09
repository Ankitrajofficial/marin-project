"use client";

import type { DataKind, Lang, Provenance } from "@/lib/types";
import { ageLabel } from "@/lib/time";
import { t } from "@/lib/i18n";

const STYLE: Record<DataKind, { bg: string; fg: string; border: string }> = {
  LIVE: { bg: "rgba(16,185,129,0.14)", fg: "#34d399", border: "rgba(16,185,129,0.45)" },
  CACHED: { bg: "rgba(245,158,11,0.14)", fg: "#fbbf24", border: "rgba(245,158,11,0.45)" },
  SNAPSHOT: { bg: "rgba(251,146,60,0.16)", fg: "#fb923c", border: "rgba(251,146,60,0.55)" },
  ARCHIVE: { bg: "rgba(167,139,250,0.16)", fg: "#c4b5fd", border: "rgba(167,139,250,0.5)" },
  DERIVED: { bg: "rgba(56,189,248,0.14)", fg: "#7dd3fc", border: "rgba(56,189,248,0.45)" },
};

/**
 * The provenance chip. Nothing in ORCA renders a data value without one of these beside it:
 * where the number came from, whether it is live, and how old it is. This is the product's
 * core promise made visible, not a disclaimer.
 */
export default function ProvenanceChip({
  p,
  lang = "en",
  showSource = true,
}: {
  p: Provenance;
  lang?: Lang;
  showSource?: boolean;
}) {
  const s = STYLE[p.kind] ?? STYLE.DERIVED;
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md px-2 py-[3px] text-[11px] leading-none"
      style={{ background: s.bg, border: `1px solid ${s.border}` }}
      title={[p.source, p.note, p.url].filter(Boolean).join("\n\n")}
    >
      <span className="font-semibold tracking-wide" style={{ color: s.fg }}>
        {t(lang, `prov.${p.kind}`)}
      </span>
      {showSource && (
        <span style={{ color: "var(--text-dim)" }} className="max-w-[210px] truncate">
          {p.source}
        </span>
      )}
      <span style={{ color: "var(--text-faint)" }}>· {ageLabel(p.ageSeconds)}</span>
    </span>
  );
}

/** De-duplicated chip row, used under answers and agent cards. */
export function ProvenanceRow({ items, lang = "en" }: { items: Provenance[]; lang?: Lang }) {
  const seen = new Set<string>();
  const unique = items.filter((p) => {
    const k = `${p.source}|${p.kind}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  if (!unique.length) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {unique.map((p, i) => (
        <ProvenanceChip key={`${p.source}-${i}`} p={p} lang={lang} />
      ))}
    </div>
  );
}
