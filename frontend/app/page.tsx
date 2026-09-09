"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import MapView from "@/components/MapView";
import TimeSlider from "@/components/TimeSlider";
import TracePanel from "@/components/TracePanel";
import Legend from "@/components/Legend";
import ChatPanel from "@/components/ChatPanel";
import GeofencePanel from "@/components/GeofencePanel";
import { fetchGeofence, fetchRisk, fetchTimes, fetchTrace, fetchZones } from "@/lib/api";
import type {
  CellTrace, GeofenceResponse, RiskFeatureCollection, ZoneFeatureCollection,
} from "@/lib/types";

type Bbox = [number, number, number, number];

export default function Page() {
  const [times, setTimes] = useState<string[]>([]);
  const [index, setIndex] = useState(0);
  const [bbox, setBbox] = useState<Bbox | null>(null);
  const [risk, setRisk] = useState<RiskFeatureCollection | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [zones, setZones] = useState<ZoneFeatureCollection | null>(null);
  const [point, setPoint] = useState<{ lat: number; lon: number } | null>(null);
  const [geofence, setGeofence] = useState<GeofenceResponse | null>(null);
  const [gfLoading, setGfLoading] = useState(false);
  const [gfError, setGfError] = useState<string | null>(null);

  const [hlCells, setHlCells] = useState<string[]>([]);
  const [hlZones, setHlZones] = useState<string[]>([]);

  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<CellTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  useEffect(() => {
    fetchTimes()
      .then((t) => { setTimes(t.times); setIndex(Math.max(0, t.times.length - 1)); })
      .catch((e) => setError(`Cannot reach the API: ${e.message}`));
  }, []);

  // Boundaries are static; fetch once. Failure is reported, never faked --
  // a missing IMBL on the map must not look like an absent IMBL in the water.
  useEffect(() => {
    fetchZones().then(setZones).catch((e) =>
      setError(`Boundaries unavailable: ${e.message}`)
    );
  }, []);

  // Every map click is a geofence query for that position.
  useEffect(() => {
    if (!point) { setGeofence(null); return; }
    setGfLoading(true); setGfError(null);
    fetchGeofence(point.lat, point.lon, 2)
      .then(setGeofence)
      .catch((e) => { setGeofence(null); setGfError(e.message); })
      .finally(() => setGfLoading(false));
  }, [point]);

  // Refetch when the viewport or the time step changes. Debounced because
  // dragging the slider or panning fires continuously.
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!bbox || times.length === 0) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      fetchRisk(bbox, times[index])
        .then((d) => { setRisk(d); setError(null); })
        // On failure the map keeps the last good data and shows the error.
        // It never invents cells -- there is no mock data anywhere here.
        .catch((e) => setError(e.message));
    }, 180);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [bbox, index, times]);

  useEffect(() => {
    if (!selected || times.length === 0) { setTrace(null); return; }
    setTraceLoading(true); setTraceError(null);
    fetchTrace(selected, times[index])
      .then(setTrace)
      .catch((e) => { setTrace(null); setTraceError(e.message); })
      .finally(() => setTraceLoading(false));
  }, [selected, index, times]);

  const onBboxChange = useCallback((b: Bbox) => setBbox(b), []);
  const onCellClick = useCallback((c: string) => setSelected(c), []);
  const onMapClick = useCallback(
    (lat: number, lon: number) => setPoint({ lat, lon }), []
  );
  const onHighlight = useCallback((cells: string[], zones: string[]) => {
    setHlCells(cells); setHlZones(zones);
  }, []);
  const closePanel = useCallback(() => {
    setSelected(null); setPoint(null); setGeofence(null); setGfError(null);
  }, []);
  const panelOpen = point !== null || selected !== null;

  return (
    <main className="shell">
      {/* Collection-level guard: if ANY cell in view is simulated, the whole
          view is banded, not just the cell's panel. */}
      {risk?.simulated && (
        <div className="simbar">
          SIMULATED SCENARIO DATA IN VIEW — NOT A REAL FORECAST
        </div>
      )}

      <MapView
        data={risk}
        zones={zones}
        selectedCell={selected}
        onBboxChange={onBboxChange}
        onCellClick={onCellClick}
        onMapClick={onMapClick}
        marker={point}
        highlightCells={hlCells}
        highlightZones={hlZones}
      />

      <ChatPanel onHighlight={onHighlight} />

      <div className="topbar">
        <div className="brand">
          ORCA <small>marine hazard field · Kerala–Tamil Nadu</small>
        </div>
        <div className="hint">
          {risk ? `${risk.n_features} cells` : "…"}
          {zones ? ` · ${zones.n_features} boundaries` : ""} · click the map for
          a position check
        </div>
      </div>

      {error && <div className="card err">{error}</div>}

      <Legend observedMax={risk?.hazard_prob_max ?? null} />

      <TimeSlider
        times={times}
        index={index}
        onChange={setIndex}
        servedTime={risk?.valid_time ?? null}
      />

      {panelOpen && (
        <aside className="panel">
          <button className="close" onClick={closePanel}>×</button>
          <h2>Position report</h2>
          <GeofencePanel geofence={geofence} loading={gfLoading} error={gfError} />
          <TracePanel trace={trace} loading={traceLoading} error={traceError} />
        </aside>
      )}
    </main>
  );
}
