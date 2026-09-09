"use client";

import { useEffect, useRef } from "react";
import maplibregl, { Map as MlMap } from "maplibre-gl";
import type { RiskFeatureCollection } from "@/lib/types";
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

export default function MapView({
  data, selectedCell, onBboxChange, onCellClick,
}: {
  data: RiskFeatureCollection | null;
  selectedCell: string | null;
  onBboxChange: (bbox: [number, number, number, number]) => void;
  onCellClick: (cell: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const map = useRef<MlMap | null>(null);
  // Callbacks live in refs so the map is built exactly once; rebuilding it on
  // every render would fight MapLibre's own lifecycle and drop the viewport.
  const cbs = useRef({ onBboxChange, onCellClick });
  cbs.current = { onBboxChange, onCellClick };

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
    if (!m || !m.getLayer("risk-selected")) return;
    m.setFilter("risk-selected", ["==", ["get", "h3_cell"], selectedCell ?? ""]);
  }, [selectedCell]);

  return <div id="map" ref={ref} />;
}
