"use client";

import type { GeofenceResponse } from "@/lib/types";

const VERDICT_LABEL: Record<string, string> = {
  inside: "INSIDE A ZONE",
  alert: "APPROACHING A BOUNDARY",
  clear: "CLEAR",
};

const compass = (deg: number | null) => {
  if (deg === null) return "";
  const pts = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"];
  return `${pts[Math.round(deg / 22.5) % 16]} ${deg.toFixed(0)}°`;
};

export default function GeofencePanel({
  geofence, loading, error,
}: { geofence: GeofenceResponse | null; loading: boolean; error: string | null }) {
  if (loading) return <><h3>Boundaries</h3><p className="hint">Checking…</p></>;
  if (error) return <><h3>Boundaries</h3><div className="warn">{error}</div></>;
  if (!geofence) return null;

  const g = geofence;
  const cls = g.verdict === "clear" ? "self" : "nb";

  return (
    <>
      <h3>Boundaries</h3>

      {/* Official IMD warnings first: they are authoritative, unlike every
          other zone here, and IMD requires explicit attribution wherever they
          appear. */}
      {g.official_alerts.length > 0 && (
        <div className="official">
          <div className="official-head">
            ◆ OFFICIAL WARNING — India Meteorological Department
          </div>
          {g.official_alerts.map((a) => (
            <div key={a.zone_id} className="official-body">
              <b>{a.event ?? a.name}</b>
              {a.severity && ` · severity ${a.severity}`}
              {a.verdict === "inside" ? " · you are inside this area" :
                ` · ${a.distance_nm} NM away`}
              {a.expires && (
                <><br />in force until {new Date(a.expires).toLocaleString(
                  undefined, { timeZone: "Asia/Kolkata" })} IST</>
              )}
              {a.sender_name && <><br />{a.sender_name}</>}
              {/* Mandatory attribution, rendered with the warning, never
                  tucked into a footer. */}
              <div className="attrib">{a.attribution}</div>
            </div>
          ))}
        </div>
      )}

      {/* The boundary guard. These lines are open data, not legal boundaries,
          and that must be visible next to the number -- not in a tooltip. */}
      {g.boundaries_advisory_only && (
        <div className="warn" style={{ marginTop: 0 }}>
          <b>ADVISORY ONLY.</b> Open-data boundaries (MarineRegions/VLIZ,
          OpenStreetMap). Not Survey of India. No legal authority. Do not use
          for navigation or enforcement.
        </div>
      )}

      <p style={{ margin: "6px 0 10px" }}>
        <span className={`badge ${cls}`}>{VERDICT_LABEL[g.verdict] ?? g.verdict}</span>{" "}
        <span className="hint">
          {g.lat.toFixed(4)}, {g.lon.toFixed(4)} · {g.buffer_nm} NM buffer
        </span>
      </p>

      {g.alerts.length > 0 && (
        <table>
          <thead>
            <tr><th>zone</th><th className="num">distance</th><th>bearing</th></tr>
          </thead>
          <tbody>
            {g.alerts.map((h) => (
              <tr key={h.zone_id}>
                <td>
                  {h.name ?? h.zone_type}
                  <br />
                  <span className="cell">{h.zone_type}</span>{" "}
                  {h.inside && <span className="badge nb">inside</span>}
                </td>
                <td className="num">
                  {h.distance_nm.toFixed(2)} NM
                  <br />
                  {/* The margin is what makes "clear" mean clear. Showing it
                      keeps the caution auditable rather than mysterious. */}
                  <span className="cell">−{h.margin_nm} margin → {h.effective_distance_nm.toFixed(2)}</span>
                </td>
                <td>{compass(h.bearing_deg)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Nearest of each type</h3>
      <table>
        <tbody>
          {Object.entries(g.nearest_by_type)
            .sort((a, b) => a[1].distance_nm - b[1].distance_nm)
            .map(([zt, h]) => (
              <tr key={zt}>
                <td>
                  {zt}
                  <br />
                  <span className="cell">{(h.name ?? "").slice(0, 38)}</span>
                </td>
                <td className="num">{h.distance_nm.toFixed(2)} NM</td>
                <td>{compass(h.bearing_deg)}</td>
              </tr>
            ))}
        </tbody>
      </table>

      <p className="hint" style={{ marginTop: 8 }}>
        Distances are geodesic, to the nearest point on the boundary. A verdict
        of “clear” means clear by a margin covering boundary-data and position
        uncertainty — when in doubt, ORCA alerts.
      </p>
      {g.attributions.map((a) => (
        <p key={a} className="cell" style={{ marginTop: 6 }}>{a}</p>
      ))}
    </>
  );
}
