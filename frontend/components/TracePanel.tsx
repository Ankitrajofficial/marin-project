"use client";

import type { CellTrace } from "@/lib/types";
import { fmtAge, fmtPct } from "@/lib/color";

export default function TracePanel({
  trace, loading, error,
}: {
  trace: CellTrace | null; loading: boolean; error: string | null;
}) {
  if (loading) return <p className="hint">Loading trace…</p>;
  if (error) return <div className="warn">{error}</div>;
  if (!trace) return null;

  const d = trace.drivers;
  const vars = Object.entries(d.variables ?? {});

  return (
    <>
      <h3>Hazard cell</h3>
      <div className="cell">{trace.h3_cell}</div>

      {/* The guard, rendered. A simulated number must never read as a forecast. */}
      {trace.simulated && (
        <p style={{ margin: "12px 0 0" }}>
          <span className="badge sim">SIMULATED</span>{" "}
          <span className="hint">
            from {trace.simulated_sources.join(", ") || "scenario data"} — not a real forecast
          </span>
        </p>
      )}

      <div className="big">
        <div>
          <div className="n">{fmtPct(trace.hazard_prob)}</div>
          <div className="l">hazard probability</div>
        </div>
        <div>
          <div className="n">{(trace.uncertainty * 100).toFixed(0)}%</div>
          <div className="l">uncertainty</div>
        </div>
      </div>

      {d.lower_bound && (
        <div className="warn">
          Lower bound — {(d.missing ?? []).join(", ")} had no data anywhere nearby.
          Missing data is not safe data; the true hazard can only be higher.
        </div>
      )}
      {d.no_coverage && (
        <div className="warn">
          No observations at all for this cell. Hazard is <b>unknown</b>, not zero.
        </div>
      )}

      {vars.length > 0 && (
        <>
          <h3>What drove the number</h3>
          <table>
            <thead>
              <tr>
                <th>variable</th><th className="num">value</th>
                <th className="num">thr.</th><th className="num">P(exc)</th><th>from</th>
              </tr>
            </thead>
            <tbody>
              {vars.map(([name, v]) => (
                <tr key={name}>
                  <td>{name}</td>
                  <td className="num">{v.mu} {v.unit}</td>
                  <td className="num">{v.threshold}</td>
                  <td className="num">{fmtPct(v.p_exceed)}</td>
                  <td>
                    {v.origin === "neighbour"
                      ? <span className="badge nb">neighbour</span>
                      : <span className="badge self">this cell</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="hint" style={{ marginTop: 8 }}>
            σ from {vars[0][1].sigma_basis === "base_only"
              ? "documented constants — no ensemble, no cross-source disagreement available"
              : "cross-source disagreement plus base constants"};
            lead {vars[0][1].lead_hours.toFixed(0)} h.
          </p>
        </>
      )}

      {d.contributions && (
        <>
          <h3>Contribution (leave-one-out)</h3>
          <table>
            <tbody>
              {Object.entries(d.contributions).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td className="num">{fmtPct(v)}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {d.hazard_prob_if_fully_correlated !== undefined && (
        <>
          <h3>Assumption bounds</h3>
          <table>
            <tbody>
              <tr>
                <td>independent (used)</td>
                <td className="num">{fmtPct(d.hazard_prob ?? null)}</td>
              </tr>
              <tr>
                <td>fully correlated</td>
                <td className="num">{fmtPct(d.hazard_prob_if_fully_correlated)}</td>
              </tr>
            </tbody>
          </table>
          <p className="hint" style={{ marginTop: 8 }}>{d.combine_note}</p>
        </>
      )}

      {d.uncertainty_terms && (
        <>
          <h3>Why uncertainty is {(trace.uncertainty * 100).toFixed(0)}%</h3>
          <table>
            <tbody>
              {Object.entries(d.uncertainty_terms).map(([k, v]) => (
                <tr key={k}>
                  <td>{k.replace(/_/g, " ")}</td>
                  <td className="num">+{(v * 100).toFixed(1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>Contributing observations ({trace.observations.length})</h3>
      <table>
        <thead>
          <tr>
            <th>source</th><th>variable</th>
            <th className="num">value</th><th>age</th>
          </tr>
        </thead>
        <tbody>
          {trace.observations.map((o, i) => (
            <tr key={i}>
              <td>
                {o.source_id}
                {o.simulated && <> <span className="badge sim">SIM</span></>}
                <br />
                <span className="cell">
                  {o.origin === "neighbour" ? `borrowed ${o.from_cell}` : "this cell"}
                </span>
              </td>
              <td>{o.variable}</td>
              <td className="num">{o.value} {o.unit}</td>
              <td>{fmtAge(o.age_seconds, o.age_is_lower_bound)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint" style={{ marginTop: 8 }}>
        “at least” ages come from sources that publish no model run time, so the
        timestamp is when ORCA fetched the value — the data is at least that old.
      </p>
    </>
  );
}
