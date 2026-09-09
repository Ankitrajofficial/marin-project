"use client";

import { fmtPct } from "@/lib/color";

export default function Legend({ observedMax }: { observedMax: number | null }) {
  return (
    <div className="legend card">
      <h4>P(hazard exceeds threshold)</h4>
      <div className="bar" />
      <div className="ticks"><span>0%</span><span>50%</span><span>100%</span></div>
      <div className="note">
        Scale is fixed 0–100%, never stretched to the data.
        <br />
        Observed max this hour: <span className="obs">{fmtPct(observedMax)}</span>
        <br />
        Faded cells are uncertain. Grey = no data, which is not the same as safe.
      </div>
    </div>
  );
}
