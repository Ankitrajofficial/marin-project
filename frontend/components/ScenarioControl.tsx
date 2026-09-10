"use client";

import { useEffect, useState } from "react";
import { activateScenario, clearScenario, fetchScenarioStatus } from "@/lib/api";
import type { ScenarioStatus } from "@/lib/types";

/** Deliberately visually separate from the rest of the UI, and loud when
 *  active. A simulated hazard field that is not obviously simulated is the
 *  worst failure this system could have — worse than no scenario at all. */
export default function ScenarioControl({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<ScenarioStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const refresh = () => fetchScenarioStatus().then(setStatus).catch(() => setStatus(null));
  useEffect(() => { refresh(); }, []);

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try {
      await fn();
      await refresh();
      // The whole field changed underneath every other panel.
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  const name = status.available[0];

  return (
    <div className={`scen${status.active ? " scen-on" : ""}`}>
      <button className="scen-toggle" onClick={() => setOpen(!open)}>
        {status.active ? "◆ SIMULATION RUNNING" : "◇ Scenario"}
      </button>

      {open && (
        <div className="scen-body card">
          {status.active ? (
            <>
              <div className="scen-warn">{status.warning}</div>
              <div className="cell">
                {status.name}<br />
                {status.scenario_observations.toLocaleString()} synthetic
                observations · {status.simulated_risk_cells.toLocaleString()}{" "}
                simulated risk cells<br />
                {status.masked_real_observations.toLocaleString()} real
                observations held aside, restored on clear
              </div>
              <button className="scen-btn danger" disabled={busy}
                      onClick={() => run(clearScenario)}>
                {busy ? "clearing…" : "Clear scenario — restore real data"}
              </button>
            </>
          ) : (
            <>
              <div className="cell">
                Injects a parametric cyclone and a fleet of simulated small
                fishing craft, so the recall list has something meaningful to
                rank. Live AIS carries cargo ships, not the boats this exists
                for.
                <br /><br />
                <b>Not a forecast.</b> Everything derived from it is flagged
                simulated, and real observations it overlaps are held aside and
                put back on clear.
              </div>
              <button className="scen-btn" disabled={busy || !name}
                      onClick={() => run(() => activateScenario(name))}>
                {busy ? "activating…" : `Activate ${name}`}
              </button>
            </>
          )}
          {error && <div className="warn">{error}</div>}
        </div>
      )}
    </div>
  );
}
