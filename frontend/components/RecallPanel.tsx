"use client";

import { useEffect, useState } from "react";
import { fetchRecall } from "@/lib/api";
import type { RecallEntry, RecallResponse } from "@/lib/types";

const hrs = (h: number | null) =>
  h === null ? "—" : h < 1 ? `${(h * 60).toFixed(0)} min` : `${h.toFixed(1)} h`;

/** Colour by slack, not by weather. A negative margin means the vessel
 *  cannot reach shelter before the hazard — that is the alarm. */
const marginClass = (m: number | null) =>
  m === null ? "self" : m < 0 ? "sim" : m < 3 ? "nb" : "self";

function Row({ e, selected, onSelect }: {
  e: RecallEntry; selected: boolean; onSelect: (e: RecallEntry) => void;
}) {
  return (
    <div className={`vrow${selected ? " vrow-sel" : ""}`} onClick={() => onSelect(e)}>
      <div className="vrow-top">
        <b>{e.name || "unidentified"}</b>
        <span className={`badge ${marginClass(e.margin_h)}`}>
          {e.margin_h === null ? "no crossing" : `margin ${hrs(e.margin_h)}`}
        </span>
      </div>
      <div className="cell">
        {e.mmsi} · {e.vessel_class || "class unknown"}
        {e.simulated && <> · <span className="badge sim">SIM</span></>}
      </div>
      <div className="vrow-nums">
        <span>to harbour <b>{hrs(e.time_to_harbour_h)}</b></span>
        <span>to hazard <b>{hrs(e.time_to_hazard_h)}</b></span>
      </div>
      {e.harbour && (
        <div className="cell">
          → {e.harbour.name} ({e.harbour.distance_nm} NM)
          {/* draft_ok === null means unverifiable. Never a tick. */}
          {e.harbour.draft_ok === null && " · depth unverified"}
        </div>
      )}
      {selected && (
        <div className="vdetail">
          {e.hazard_driver && (
            <div>driven by <b>{e.hazard_driver}</b> at{" "}
              {e.hazard_prob_at_crossing !== null
                ? `${(e.hazard_prob_at_crossing * 100).toFixed(1)}%` : "—"}
              {e.hazard_time && ` (${new Date(e.hazard_time).toISOString().slice(5, 16)}Z)`}
            </div>
          )}
          <div>
            position {e.position_age_minutes.toFixed(0)} min old
            {e.position_is_stale && " (STALE)"} · could be up to{" "}
            <b>{e.position_uncertainty_nm} NM</b> from here
          </div>
          <div>
            speed {e.speed_ms} m/s ({e.speed_source.replace("_", " ")}
            {e.speed_samples ? `, n=${e.speed_samples}` : ""})
            {e.draft_m !== null ? ` · draught ${e.draft_m} m` : " · draught unknown"}
          </div>
          {e.hazard_data_age_minutes !== null && (
            <div>hazard data {e.hazard_data_age_minutes.toFixed(0)} min old</div>
          )}
          {e.reasons.map((r) => <div key={r}>· {r}</div>)}
          {e.flags.length > 0 && <div className="cell">flags: {e.flags.join(", ")}</div>}
        </div>
      )}
    </div>
  );
}

export default function RecallPanel({
  selected, onSelect,
}: {
  selected: RecallEntry | null;
  onSelect: (e: RecallEntry | null) => void;
}) {
  const [data, setData] = useState<RecallResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [threshold, setThreshold] = useState(0.05);

  useEffect(() => {
    fetchRecall(threshold).then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message));
  }, [threshold]);

  if (error) return <div className="warn">{error}</div>;
  if (!data) return <p className="hint">Loading recall list…</p>;

  return (
    <>
      <h3>Recall priority</h3>
      <p className="hint" style={{ marginTop: 0 }}>
        Ranked by slack: time to hazard minus time to harbour. Smallest first.
      </p>

      {data.simulated && (
        <p><span className="badge sim">SIMULATED</span></p>
      )}

      <label className="hint" style={{ display: "block", margin: "10px 0" }}>
        hazard threshold {(threshold * 100).toFixed(0)}%
        <input type="range" min={1} max={50} value={threshold * 100}
               onChange={(e) => setThreshold(Number(e.target.value) / 100)}
               style={{ width: "100%", accentColor: "var(--accent)" }} />
      </label>

      <div className="cell" style={{ marginBottom: 10 }}>
        {data.n_vessels} vessel(s) · {data.n_harbours} harbours ·
        straight-line ×{data.detour_factor}
      </div>

      {data.ranked.map((e) => (
        <Row key={e.mmsi} e={e} selected={selected?.mmsi === e.mmsi}
             onSelect={(x) => onSelect(selected?.mmsi === x.mmsi ? null : x)} />
      ))}

      {/* Deliberately its own section. A vessel we could not evaluate must
          reach a human, not sit at the bottom of a list read from the top. */}
      {data.cannot_assess.length > 0 && (
        <>
          <h3>Could not assess — not ranked as safe</h3>
          {data.cannot_assess.map((e) => (
            <Row key={e.mmsi} e={e} selected={selected?.mmsi === e.mmsi}
                 onSelect={(x) => onSelect(selected?.mmsi === x.mmsi ? null : x)} />
          ))}
        </>
      )}

      <h3>Limits of this list</h3>
      <ul className="caveats">
        {data.caveats.map((c) => <li key={c}>{c}</li>)}
      </ul>
    </>
  );
}
