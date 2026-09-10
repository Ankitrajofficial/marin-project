"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import MapView from "@/components/MapView";
import TimeSlider from "@/components/TimeSlider";
import TracePanel from "@/components/TracePanel";
import Legend from "@/components/Legend";
import ChatPanel from "@/components/ChatPanel";
import GeofencePanel from "@/components/GeofencePanel";
import RecallPanel from "@/components/RecallPanel";
import ScenarioControl from "@/components/ScenarioControl";
import VerdictBanner from "@/components/VerdictBanner";
import { recallSummary } from "@/lib/verdict";
import { fetchGeofence, fetchRisk, fetchTimes, fetchTrace, fetchZones } from "@/lib/api";
import type {
  CellTrace, GeofenceResponse, RecallEntry, RecallResponse,
  RiskFeatureCollection, ScenarioStatus, ZoneFeatureCollection,
} from "@/lib/types";
import { fetchRecall } from "@/lib/api";

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

  const [tab, setTab] = useState<"position" | "recall">("position");
  // Bumped when a scenario is activated or cleared: every panel is showing a
  // field that has just been replaced wholesale.
  const [worldVersion, setWorldVersion] = useState(0);
  const [recall, setRecall] = useState<RecallResponse | null>(null);
  const [vessel, setVessel] = useState<RecallEntry | null>(null);

  const [hlCells, setHlCells] = useState<string[]>([]);
  const [hlZones, setHlZones] = useState<string[]>([]);

  const [selected, setSelected] = useState<string | null>(null);
  const [trace, setTrace] = useState<CellTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  // Nearest step to NOW, not the last one. The slider spans three days of
  // forecast, so opening on times.length - 1 answered "what will Saturday
  // night look like" to someone who asked "can I go out". A hazard map's
  // default view is the present hour.
  const nearestToNow = (list: string[]) => {
    if (list.length === 0) return 0;
    const now = Date.now();
    let best = 0, bestGap = Infinity;
    list.forEach((iso, i) => {
      const gap = Math.abs(new Date(iso).getTime() - now);
      if (gap < bestGap) { bestGap = gap; best = i; }
    });
    return best;
  };

  const timesRef = useRef<string[]>([]);
  timesRef.current = times;

  useEffect(() => {
    fetchTimes()
      .then((t) => { setTimes(t.times); setIndex(nearestToNow(t.times)); })
      .catch((e) => setError(`Cannot reach the API: ${e.message}`));
  }, []);

  // A scenario replaces the world for a WINDOW of hours. Landing outside it is
  // the worst of both: the banner says a simulation is running while the map
  // shows real data, which is precisely the confusion the simulated flag
  // exists to prevent. Jump to the first step the scenario actually covers.
  const onScenarioChanged = useCallback((status: ScenarioStatus | null) => {
    setWorldVersion((v) => v + 1);
    setVessel(null);
    const start = status?.active
      ? (status.params?.start as string | undefined)
      : undefined;
    if (!start) return;
    const t0 = new Date(start).getTime();
    fetchTimes()
      .then((t) => {
        setTimes(t.times);
        const i = t.times.findIndex((iso) => new Date(iso).getTime() >= t0);
        setIndex(i >= 0 ? i : nearestToNow(t.times));
      })
      .catch(() => {
        // Times unchanged is the normal case; fall back to what we have.
        const i = timesRef.current.findIndex((iso) => new Date(iso).getTime() >= t0);
        if (i >= 0) setIndex(i);
      });
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
  }, [bbox, index, times, worldVersion]);

  useEffect(() => {
    if (!selected || times.length === 0) { setTrace(null); return; }
    setTraceLoading(true); setTraceError(null);
    fetchTrace(selected, times[index])
      .then(setTrace)
      .catch((e) => { setTrace(null); setTraceError(e.message); })
      .finally(() => setTraceLoading(false));
  }, [selected, index, times]);

  // Vessel positions are drawn whichever tab is open, so a hazard cell and the
  // boats inside it are visible together rather than in separate views.
  useEffect(() => {
    fetchRecall(0.05).then(setRecall).catch(() => setRecall(null));
  }, [worldVersion]);

  const onBboxChange = useCallback((b: Bbox) => setBbox(b), []);
  const onCellClick = useCallback((c: string) => setSelected(c), []);
  const onMapClick = useCallback(
    (lat: number, lon: number) => setPoint({ lat, lon }), []
  );
  const openRecall = useCallback(() => setTab("recall"), []);
  // Clicking a boat is the obvious way to ask "what about that one", and it
  // opens the same panel the list uses so the two stay in step.
  const onVesselClick = useCallback((mmsi: string) => {
    setRecall((r) => {
      const hit = r
        ? [...r.ranked, ...r.cannot_assess].find((v) => v.mmsi === mmsi)
        : null;
      if (hit) { setVessel(hit); setTab("recall"); }
      return r;
    });
  }, []);
  const onHighlight = useCallback((cells: string[], zones: string[]) => {
    setHlCells(cells); setHlZones(zones);
  }, []);
  const closePanel = useCallback(() => {
    setSelected(null); setPoint(null); setGeofence(null); setGfError(null);
    setTab("position"); setVessel(null);
  }, []);
  const panelOpen = point !== null || selected !== null || tab === "recall";

  return (
    <main className={`shell${risk?.simulated ? " simulated" : ""}`}>
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
        vessels={recall ? [...recall.ranked, ...recall.cannot_assess] : []}
        selectedVessel={vessel}
        onVesselClick={onVesselClick}
      />

      <ChatPanel onHighlight={onHighlight} />

      <div className="topbar">
        <div className="brand">
          ORCA <small>marine hazard field · Kerala–Tamil Nadu</small>
        </div>
        <ScenarioControl onChanged={onScenarioChanged} />
        <div className="hint">
          <div>
            {risk ? `${risk.n_features} cells` : "…"}
            {zones ? ` · ${zones.n_features} boundaries` : ""}
            {recall && (
              <>
                {" · "}
                {/* The recall list used to be reachable only by clicking the map
                    first, because the panel that holds its tab opened on a
                    position click. The prioritised recall list IS the disaster
                    -management deliverable; it cannot be behind an undocumented
                    gesture. */}
                <button className="linkish" onClick={openRecall}>
                  {recall.n_vessels} vessels — recall list
                </button>
              </>
            )}
            {" · click the map for a position check"}
          </div>
          {/* The count alone ("52 vessels") reads as inventory. The number that
              decides whether anyone launches a boat is how many of them cannot
              get in, so say that in words rather than leaving it one click
              away inside the panel. */}
          {recall && (
            <div className="recall-read">
              {recallSummary(recall.ranked, recall.cannot_assess.length, recall.n_vessels)}
            </div>
          )}
        </div>
      </div>

      <VerdictBanner risk={risk} />

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
          <div className="tabs">
            <button className={tab === "position" ? "on" : ""}
                    onClick={() => setTab("position")}>Position</button>
            <button className={tab === "recall" ? "on" : ""}
                    onClick={() => setTab("recall")}>
              Recall{recall ? ` (${recall.n_vessels})` : ""}
            </button>
          </div>
          {tab === "position" ? (
            <>
              <GeofencePanel geofence={geofence} loading={gfLoading} error={gfError} />
              <TracePanel trace={trace} loading={traceLoading} error={traceError} />
            </>
          ) : (
            <RecallPanel selected={vessel} onSelect={setVessel} />
          )}
        </aside>
      )}
    </main>
  );
}
