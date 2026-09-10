"use client";

import { useEffect, useRef, useState } from "react";
import maplibregl, { Map as MlMap } from "maplibre-gl";
import type { RecallEntry, RiskFeatureCollection, ZoneFeatureCollection } from "@/lib/types";
import { HAZARD_STOPS, NO_DATA_COLOR, SIMULATED_OUTLINE } from "@/lib/color";

// OSM raster tiles. No Mapbox, no API key, no token. Attribution is required
// by the OSM tile usage policy and is rendered by MapLibre from this source.
const STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

const EMPTY: RiskFeatureCollection = {
  type: "FeatureCollection", valid_time: "", requested_time: null,
  simulated: false, n_features: 0, hazard_prob_max: null, features: [],
};

// Kerala -> Tamil Nadu coast.
//
// Bounds, not a centre and a zoom. A fixed zoom frames a different area on
// every screen: on a wide monitor 6.1 reached past the Andamans to Myanmar,
// water this system holds no data for and makes no claim about. Framing by
// bounds shows the SAME area everywhere and lets the viewport pick the zoom.
//
// Matches the extent of what is actually ingested (app/aoi.py spans lat
// 8.1-12.9, lon 74.8-80.5; observations reach lat 7.0-13.9, lon 74.0-82.5),
// with a small margin so coastal cells are not flush against the edge.
export const AOI_BOUNDS: [[number, number], [number, number]] =
  [[73.2, 6.4], [82.9, 14.4]];

// How far the user may roam. Generous enough to see context around the AOI,
// tight enough that the Andamans (~92E) and Myanmar (~95E) stay off screen:
// empty ocean there is not "no hazard", it is "not modelled", and a map that
// pans to blankness invites the first reading.
const MAX_BOUNDS: [[number, number], [number, number]] =
  [[70.5, 3.5], [86.0, 17.5]];

// Stops the user zooming out until the AOI is a speck in a blank Indian Ocean.
const MIN_ZOOM = 5;

// Boundary styling. Each zone type is visually distinct because they mean
// different things: crossing an IMBL is a legal event, crossing the 24 NM line
// is not. Dashes mark LINE zones (a treaty boundary is a line, not an area).
const ZONE_LINE_COLOR: (string | string[])[] = [
  "match", ["get", "zone_type"],
  "imbl", "#ff3b30",
  "baseline", "#ff9f0a",
  "territorial_sea", "#5ac8fa",
  "contiguous_zone", "#3a86c8",
  "eez", "#8e8e93",
  "mpa", "#34c759",
  // Official IMD warning: distinct from every advisory boundary on the map.
  "imd_warning", "#ff9500",
  "#8e8e93",
];

export default function MapView({
  data, zones, selectedCell, onBboxChange, onCellClick, onMapClick, marker,
  highlightCells, highlightZones, vessels, selectedVessel, onVesselClick,
}: {
  data: RiskFeatureCollection | null;
  zones: ZoneFeatureCollection | null;
  selectedCell: string | null;
  highlightCells: string[];
  highlightZones: string[];
  vessels: RecallEntry[];
  selectedVessel: RecallEntry | null;
  onBboxChange: (bbox: [number, number, number, number]) => void;
  onCellClick: (cell: string) => void;
  onMapClick: (lat: number, lon: number) => void;
  onVesselClick: (mmsi: string) => void;
  marker: { lat: number; lon: number } | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  const resizeObs = useRef<ResizeObserver | null>(null);
  // True once the load handler below has added every source and layer.
  //
  // THIS REPLACES isStyleLoaded() AS THE GATE ON EVERY DATA EFFECT, and the
  // difference is not cosmetic. isStyleLoaded() is TRANSIENT: MapLibre reports
  // false whenever any source is still fetching, which includes the OSM raster
  // tiles streaming in for several seconds after the map is usable. An effect
  // that returns early on it has no second chance -- it only re-runs when its
  // own dependency changes, and data fetched once never changes again.
  //
  // That is exactly how 52 vessels went missing: /api/recall resolved while
  // tiles were still loading, the effect bailed, nothing re-rendered, and the
  // fleet never reached the map even though the panel counted it. Silent, and
  // invisible in the console.
  //
  // `ready` is a one-way latch on a state variable instead, so flipping it
  // re-runs every effect that is waiting, and whatever arrived early is
  // applied then.
  const [ready, setReady] = useState(false);
  // Callbacks live in refs so the map is built exactly once; rebuilding it on
  // every render would fight MapLibre's own lifecycle and drop the viewport.
  const cbs = useRef({ onBboxChange, onCellClick, onMapClick, onVesselClick });
  cbs.current = { onBboxChange, onCellClick, onMapClick, onVesselClick };
  const markerRef = useRef<maplibregl.Marker | null>(null);

  useEffect(() => {
    if (map.current || !ref.current) return;
    const m = new maplibregl.Map({
      container: ref.current, style: STYLE,
      bounds: AOI_BOUNDS, fitBoundsOptions: { padding: 24 },
      maxBounds: MAX_BOUNDS, minZoom: MIN_ZOOM,
      attributionControl: {},
    });
    map.current = m;

    // The map is absolutely positioned inside a flex row of the shell, so its
    // box changes height whenever the simulation banner appears or the verdict
    // line wraps. MapLibre only watches the window, not the container, and a
    // stale size shows as a half-painted canvas with clicks landing off-target.
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(ref.current);
    resizeObs.current = ro;
    m.addControl(new maplibregl.NavigationControl({}), "bottom-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    m.on("load", () => {
      m.addSource("risk", { type: "geojson", data: EMPTY });
      m.addSource("vessels", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection,
      });
      m.addSource("route", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection,
      });
      m.addSource("zones", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] } as GeoJSON.FeatureCollection,
      });

      m.addLayer({
        id: "risk-fill", type: "fill", source: "risk",
        paint: {
          // Colour ramp over a FIXED 0 -> 1 domain. Never rescaled to the
          // observed range -- see lib/color.ts for why that matters.
          "fill-color": [
            "case",
            ["==", ["get", "hazard_prob"], null], NO_DATA_COLOR,
            ["interpolate", ["linear"], ["get", "hazard_prob"],
              ...HAZARD_STOPS.flatMap(([stop, c]) => [stop, c])],
          ],
          // Confidence made visible: an uncertain cell is literally faint.
          "fill-opacity": [
            "max", 0.12,
            ["-", 1, ["coalesce", ["get", "uncertainty"], 0]],
          ],
        },
      });

      m.addLayer({
        id: "risk-line", type: "line", source: "risk",
        paint: {
          // A simulated cell is outlined in magenta on the map itself. A badge
          // in a side panel can be missed while panning; this cannot.
          "line-color": ["case", ["get", "simulated"], SIMULATED_OUTLINE, "#5b7085"],
          "line-width": ["case", ["get", "simulated"], 2.5, 0.6],
        },
      });

      // Cells and zones an answer referred to. Separate from the click
      // selection so a chat answer can light up several at once without
      // fighting whatever the user last clicked.
      m.addLayer({
        id: "risk-highlight", type: "line", source: "risk",
        filter: ["in", ["get", "h3_cell"], ["literal", []]],
        paint: { "line-color": "#4da3d4", "line-width": 3 },
      });
      m.addLayer({
        id: "zone-highlight", type: "line", source: "zones",
        filter: ["in", ["get", "zone_id"], ["literal", []]],
        paint: { "line-color": "#ffffff", "line-width": 3.5 },
      });

      // The straight-line leg to the target harbour. DASHED on purpose: it
      // is not a route, and drawing it solid would imply routing.py exists.
      m.addLayer({
        id: "route-line", type: "line", source: "route",
        filter: ["==", ["geometry-type"], "LineString"],
        // Solid = a real routed track. Dashed = a straight-line ESTIMATE,
        // because drawing an estimate solid would imply a plan that does not
        // exist.
        paint: {
          "line-color": "#ffd60a",
          "line-width": ["case", ["get", "routed"], 3, 2],
          "line-dasharray": ["case", ["get", "routed"],
            ["literal", [1, 0]], ["literal", [2, 2]]],
        },
      });
      // How far the vessel could actually be, given the age of its last fix.
      m.addLayer({
        id: "vessel-uncertainty", type: "circle", source: "vessels",
        paint: {
          "circle-radius": ["max", 4, ["*", ["get", "uncertainty_nm"], 2.2]],
          "circle-color": "#4da3d4", "circle-opacity": 0.12,
          "circle-stroke-width": 0.5, "circle-stroke-color": "#4da3d4",
        },
      });
      m.addLayer({
        id: "vessel-dot", type: "circle", source: "vessels",
        paint: {
          // Big enough to see against the hazard ramp and to hit with a
          // mouse: these are click targets now, not decoration.
          "circle-radius": ["case", ["get", "selected"], 9, 6],
          "circle-color": ["case",
            ["get", "simulated"], "#c026d3",
            ["get", "urgent"], "#d9534f",
            "#e6edf3"],
          "circle-stroke-width": 1.5, "circle-stroke-color": "#10151c",
        },
      });
      m.addLayer({
        id: "harbour-dot", type: "circle", source: "route",
        filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 6, "circle-color": "#ffd60a",
                 "circle-stroke-width": 1.5, "circle-stroke-color": "#10151c" },
      });

      m.addLayer({
        id: "risk-selected", type: "line", source: "risk",
        filter: ["==", ["get", "h3_cell"], ""],
        paint: { "line-color": "#ffffff", "line-width": 2.5 },
      });

      // MPA fill sits UNDER the risk layer so hazard colour stays readable.
      m.addLayer({
        id: "zone-fill", type: "fill", source: "zones",
        filter: ["in", ["get", "zone_type"], ["literal", ["mpa", "imd_warning"]]],
        paint: {
          "fill-color": ["case",
            ["==", ["get", "zone_type"], "imd_warning"], "#ff9500", "#34c759"],
          "fill-opacity": ["case",
            ["==", ["get", "zone_type"], "imd_warning"], 0.22, 0.14],
        },
      }, "risk-fill");
      m.addLayer({
        id: "zone-line", type: "line", source: "zones",
        paint: {
          "line-color": ZONE_LINE_COLOR as unknown as maplibregl.ExpressionSpecification,
          "line-width": ["match", ["get", "zone_type"], "imbl", 2.4, "imd_warning", 2.0, 1.2],
          "line-dasharray": ["match", ["get", "zone_type"],
            "imbl", ["literal", [3, 2]],
            "baseline", ["literal", [1, 2]],
            ["literal", [1, 0]]],
        },
      });

      // Any click anywhere is a geofence query: a boat is at a POSITION, not
      // in a hazard cell, and the boundary question applies over open water
      // where no risk cell has been computed.
      m.on("click", (e) => {
        // A click that landed on a boat is a question about THAT BOAT, not
        // about the water under it. Without this test both handlers fire and
        // the position panel elbows the recall panel aside on every vessel
        // click.
        if (m.queryRenderedFeatures(e.point, { layers: ["vessel-dot"] }).length) return;
        cbs.current.onMapClick(e.lngLat.lat, e.lngLat.lng);
      });

      m.on("click", "vessel-dot", (e) => {
        const f = e.features?.[0];
        if (f) cbs.current.onVesselClick(f.properties!.mmsi as string);
      });
      m.on("mouseenter", "vessel-dot", () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", "vessel-dot", () => { m.getCanvas().style.cursor = ""; });

      m.on("click", "risk-fill", (e) => {
        const f = e.features?.[0];
        if (f) cbs.current.onCellClick(f.properties!.h3_cell as string);
      });
      m.on("mouseenter", "risk-fill", () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", "risk-fill", () => { m.getCanvas().style.cursor = ""; });

      const emit = () => {
        const b = m.getBounds();
        cbs.current.onBboxChange([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
      };
      emit();
      m.on("moveend", emit);

      // Last: every source and layer above now exists, so the data effects
      // may run.
      setReady(true);
    });

    return () => {
      setReady(false);
      resizeObs.current?.disconnect(); resizeObs.current = null;
      m.remove(); map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const src = m.getSource("risk") as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData((data ?? EMPTY) as unknown as GeoJSON.FeatureCollection);
  }, [data, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const src = m.getSource("zones") as maplibregl.GeoJSONSource | undefined;
    if (src && zones) src.setData(zones as unknown as GeoJSON.FeatureCollection);
  }, [zones, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    markerRef.current?.remove();
    markerRef.current = null;
    if (marker) {
      markerRef.current = new maplibregl.Marker({ color: "#ffffff" })
        .setLngLat([marker.lon, marker.lat])
        .addTo(m);
    }
  }, [marker]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !m.getLayer("risk-selected")) return;
    m.setFilter("risk-selected", ["==", ["get", "h3_cell"], selectedCell ?? ""]);
  }, [selectedCell, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready) return;
    const vs = m.getSource("vessels") as maplibregl.GeoJSONSource | undefined;
    if (vs) {
      vs.setData({
        type: "FeatureCollection",
        features: vessels.map((v) => ({
          type: "Feature" as const,
          geometry: { type: "Point" as const, coordinates: [v.lon, v.lat] },
          properties: {
            mmsi: v.mmsi,
            uncertainty_nm: v.position_uncertainty_nm,
            simulated: v.simulated,
            urgent: v.margin_h !== null && v.margin_h < 3,
            selected: selectedVessel?.mmsi === v.mmsi,
          },
        })),
      });
    }
    const rs = m.getSource("route") as maplibregl.GeoJSONSource | undefined;
    if (rs) {
      const v = selectedVessel;
      // A real routed path when routing found one; otherwise the straight
      // line, which the dashed style marks as an estimate rather than a track.
      const line = v
        ? (v.route_coordinates.length > 1
            ? v.route_coordinates
            : (v.harbour ? [[v.lon, v.lat], [v.harbour.lon, v.harbour.lat]] : []))
        : [];
      rs.setData({
        type: "FeatureCollection",
        features: v && v.harbour && line.length > 1 ? [
          { type: "Feature" as const,
            geometry: { type: "LineString" as const, coordinates: line },
            properties: { routed: v.time_is_routed } },
          { type: "Feature" as const,
            geometry: { type: "Point" as const,
              coordinates: [v.harbour.lon, v.harbour.lat] },
            properties: { name: v.harbour.name } },
        ] : [],
      });
    }
  }, [vessels, selectedVessel, ready]);

  useEffect(() => {
    const m = map.current;
    if (!m || !ready || !m.getLayer("risk-highlight")) return;
    m.setFilter("risk-highlight",
      ["in", ["get", "h3_cell"], ["literal", highlightCells]]);
    m.setFilter("zone-highlight",
      ["in", ["get", "zone_id"], ["literal", highlightZones]]);
  }, [highlightCells, highlightZones, ready]);

  // Re-frame on the AOI. The one gesture that undoes any amount of panning and
  // zooming: without it, a user who drags off the coast has no way back to the
  // area the data covers except by guessing.
  const resetView = () => {
    map.current?.fitBounds(AOI_BOUNDS, { padding: 24, duration: 500 });
  };

  return (
    <>
      <div id="map" ref={ref} />
      <button className="mapreset" onClick={resetView}
              title="Re-frame on the Kerala-Tamil Nadu area of interest">
        Reset view
      </button>
    </>
  );
}
