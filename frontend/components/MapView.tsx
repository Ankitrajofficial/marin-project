"use client";

import { useEffect, useRef } from "react";
import maplibregl, { Map as MlMap } from "maplibre-gl";
import type { RiskFeatureCollection, ZoneFeatureCollection } from "@/lib/types";
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
const INITIAL_CENTER: [number, number] = [78.4, 10.4];
const INITIAL_ZOOM = 6.1;

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
  "#8e8e93",
];

export default function MapView({
  data, zones, selectedCell, onBboxChange, onCellClick, onMapClick, marker,
}: {
  data: RiskFeatureCollection | null;
  zones: ZoneFeatureCollection | null;
  selectedCell: string | null;
  onBboxChange: (bbox: [number, number, number, number]) => void;
  onCellClick: (cell: string) => void;
  onMapClick: (lat: number, lon: number) => void;
  marker: { lat: number; lon: number } | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  // Callbacks live in refs so the map is built exactly once; rebuilding it on
  // every render would fight MapLibre's own lifecycle and drop the viewport.
  const cbs = useRef({ onBboxChange, onCellClick, onMapClick });
  cbs.current = { onBboxChange, onCellClick, onMapClick };
  const markerRef = useRef<maplibregl.Marker | null>(null);

  useEffect(() => {
    if (map.current || !ref.current) return;
    const m = new maplibregl.Map({
      container: ref.current, style: STYLE,
      center: INITIAL_CENTER, zoom: INITIAL_ZOOM, attributionControl: {},
    });
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({}), "bottom-right");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    m.on("load", () => {
      m.addSource("risk", { type: "geojson", data: EMPTY });
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

      m.addLayer({
        id: "risk-selected", type: "line", source: "risk",
        filter: ["==", ["get", "h3_cell"], ""],
        paint: { "line-color": "#ffffff", "line-width": 2.5 },
      });

      // MPA fill sits UNDER the risk layer so hazard colour stays readable.
      m.addLayer({
        id: "zone-fill", type: "fill", source: "zones",
        filter: ["==", ["get", "zone_type"], "mpa"],
        paint: { "fill-color": "#34c759", "fill-opacity": 0.14 },
      }, "risk-fill");
      m.addLayer({
        id: "zone-line", type: "line", source: "zones",
        paint: {
          "line-color": ZONE_LINE_COLOR as unknown as maplibregl.ExpressionSpecification,
          "line-width": ["match", ["get", "zone_type"], "imbl", 2.4, 1.2],
          "line-dasharray": ["match", ["get", "zone_type"],
            "imbl", ["literal", [3, 2]],
            "baseline", ["literal", [1, 2]],
            ["literal", [1, 0]]],
        },
      });

      // Any click anywhere is a geofence query: a boat is at a POSITION, not
      // in a hazard cell, and the boundary question applies over open water
      // where no risk cell has been computed.
      m.on("click", (e) => cbs.current.onMapClick(e.lngLat.lat, e.lngLat.lng));

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
    });

    return () => { m.remove(); map.current = null; };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m || !m.isStyleLoaded()) return;
    const src = m.getSource("risk") as maplibregl.GeoJSONSource | undefined;
    if (src) src.setData((data ?? EMPTY) as unknown as GeoJSON.FeatureCollection);
  }, [data]);

  useEffect(() => {
    const m = map.current;
    if (!m || !m.isStyleLoaded()) return;
    const src = m.getSource("zones") as maplibregl.GeoJSONSource | undefined;
    if (src && zones) src.setData(zones as unknown as GeoJSON.FeatureCollection);
  }, [zones]);

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
    if (!m || !m.getLayer("risk-selected")) return;
    m.setFilter("risk-selected", ["==", ["get", "h3_cell"], selectedCell ?? ""]);
  }, [selectedCell]);

  return <div id="map" ref={ref} />;
}
