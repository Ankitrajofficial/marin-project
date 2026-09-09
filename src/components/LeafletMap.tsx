"use client";

import { useEffect, useMemo, useState } from "react";
import { MapContainer, TileLayer, GeoJSON, CircleMarker, Popup, Polyline, useMap } from "react-leaflet";
import type { GeoJsonObject } from "geojson";
import type { MapPayload } from "@/lib/types";
import "leaflet/dist/leaflet.css";

interface Layers {
  pfz: GeoJsonObject & { meta?: Record<string, unknown> };
  imbl: GeoJsonObject & { meta?: Record<string, unknown> };
  mpa: GeoJsonObject & { meta?: Record<string, unknown> };
  coastline: GeoJsonObject;
  eez: GeoJsonObject;
  harbours: { items: Array<{ id: string; name: string; lat: number; lon: number; state: string }> };
}

const MARKER_STYLE = {
  query: { color: "#22d3ee", fill: "#22d3ee", r: 9 },
  destination: { color: "#f0abfc", fill: "#f0abfc", r: 8 },
  harbour: { color: "#94a3b8", fill: "#64748b", r: 6 },
  pfz: { color: "#34d399", fill: "#10b981", r: 7 },
  hazard: { color: "#f43f5e", fill: "#f43f5e", r: 8 },
} as const;

/** Recentres the map whenever a new query lands, without remounting the whole container. */
function Recentre({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.flyTo(center, zoom, { duration: 0.8 });
  }, [map, center, zoom]);
  return null;
}

const bandColor = (b: string) =>
  b === "UNSAFE" ? "#f43f5e" : b === "CAUTION" ? "#f59e0b" : b === "UNKNOWN" ? "#94a3b8" : "#10b981";

export default function LeafletMap({
  payload,
  visible,
}: {
  payload: MapPayload | null;
  visible: Record<string, boolean>;
}) {
  const [layers, setLayers] = useState<Layers | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/layers")
      .then((r) => r.json())
      .then((d: Layers) => alive && setLayers(d))
      .catch(() => {
        /* map degrades to basemap only; never blocks the demo */
      });
    return () => {
      alive = false;
    };
  }, []);

  const center = payload?.center ?? ([13.0, 80.2] as [number, number]);
  const zoom = payload?.zoom ?? 6;
  const highlight = useMemo(() => new Set(payload?.highlightPfzIds ?? []), [payload]);

  return (
    <MapContainer
      center={center}
      zoom={zoom}
      className="h-full w-full"
      scrollWheelZoom
      preferCanvas
      worldCopyJump={false}
    >
      {/* CARTO dark basemap: free, keyless, no token. */}
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; OpenStreetMap contributors &copy; CARTO'
        maxZoom={18}
      />
      <Recentre center={center} zoom={zoom} />

      {layers && visible.eez && (
        <GeoJSON
          key="eez"
          data={layers.eez}
          style={{ color: "#1e6a8d", weight: 1, fillColor: "#0e7490", fillOpacity: 0.06, dashArray: "6 6" }}
        />
      )}

      {layers && visible.coastline && (
        <GeoJSON key="coastline" data={layers.coastline} style={{ color: "#5b8cb8", weight: 1.4, opacity: 0.75 }} />
      )}

      {layers && visible.mpa && (
        <GeoJSON
          key="mpa"
          data={layers.mpa}
          style={{ color: "#a78bfa", weight: 1.5, fillColor: "#7c3aed", fillOpacity: 0.18 }}
          onEachFeature={(f, l) => {
            const p = f.properties as Record<string, string>;
            l.bindPopup(
              `<b>${p.name}</b><br/>${p.designation}<br/><i>${p.rule}</i><br/><span style="color:#fb923c">CACHED · simplified polygon, not a gazetted boundary</span>`,
            );
          }}
        />
      )}

      {layers && visible.pfz && (
        <GeoJSON
          key="pfz"
          data={layers.pfz}
          style={(f) => {
            const on = highlight.has(String(f?.properties?.id));
            return {
              color: on ? "#34d399" : "#0f9b76",
              weight: on ? 2.5 : 1,
              fillColor: "#10b981",
              fillOpacity: on ? 0.36 : 0.14,
            };
          }}
          onEachFeature={(f, l) => {
            const p = f.properties as Record<string, string | number>;
            l.bindPopup(
              `<b>Potential Fishing Zone</b><br/>near ${p.nearest_harbour}<br/>` +
                `score ${p.score} (${p.confidence}) · SST ${p.sst_c} °C · chl-a ${p.chlorophyll_a_mg_m3} mg/m³<br/>` +
                `depth band ${p.depth_band_m} m<br/>` +
                `<span style="color:#fb923c">CACHED · demo derivation, NOT an INCOIS advisory</span>`,
            );
          }}
        />
      )}

      {layers && visible.imbl && (
        <GeoJSON
          key="imbl"
          data={layers.imbl}
          style={{ color: "#f43f5e", weight: 2.5, dashArray: "8 5", opacity: 0.9 }}
          onEachFeature={(f, l) => {
            const p = f.properties as Record<string, string>;
            l.bindPopup(
              `<b>${p.name}</b><br/>${p.agreement}<br/>` +
                `<span style="color:#fb923c">CACHED · ${p.accuracy} — must not be used for navigation</span>`,
            );
          }}
        />
      )}

      {layers && visible.harbours &&
        layers.harbours.items.map((h) => (
          <CircleMarker
            key={h.id}
            center={[h.lat, h.lon]}
            radius={4}
            pathOptions={{ color: "#64748b", fillColor: "#475569", fillOpacity: 0.85, weight: 1 }}
          >
            <Popup>
              <b>{h.name}</b>
              <br />
              {h.state}
              <br />
              <span style={{ color: "#fb923c" }}>CACHED · harbour gazetteer</span>
            </Popup>
          </CircleMarker>
        ))}

      {/* Route: direct line for reference, scored polyline as the recommendation. */}
      {payload?.route && visible.route && (
        <>
          <Polyline
            positions={payload.route.direct}
            pathOptions={{ color: "#64748b", weight: 1.5, dashArray: "5 6", opacity: 0.7 }}
          />
          <Polyline positions={payload.route.recommended} pathOptions={{ color: "#22d3ee", weight: 3.5, opacity: 0.9 }} />
          {payload.route.waypoints.map((w) => (
            <CircleMarker
              key={w.index}
              center={[w.lat, w.lon]}
              radius={6}
              pathOptions={{ color: bandColor(w.band), fillColor: bandColor(w.band), fillOpacity: 0.85, weight: 2 }}
            >
              <Popup>
                <b>Waypoint {w.index}</b>
                <br />
                risk {w.riskScore}/100 · {w.band}
                <br />
                {w.waveHeightM !== null && <>wave {w.waveHeightM} m · </>}
                {w.windKmh !== null && <>wind {w.windKmh} km/h</>}
                {w.detourReason && (
                  <>
                    <br />
                    <i>{w.detourReason}</i>
                  </>
                )}
              </Popup>
            </CircleMarker>
          ))}
        </>
      )}

      {payload?.markers.map((m, i) => {
        const s = MARKER_STYLE[m.kind];
        return (
          <CircleMarker
            key={`${m.kind}-${i}`}
            center={[m.lat, m.lon]}
            radius={s.r}
            pathOptions={{ color: s.color, fillColor: s.fill, fillOpacity: 0.55, weight: 2.5 }}
          >
            <Popup>
              <b>{m.label}</b>
              {m.detail && (
                <>
                  <br />
                  {m.detail}
                </>
              )}
            </Popup>
          </CircleMarker>
        );
      })}

      {/* Boundary proximity ring, drawn only when a boundary rule is live. */}
      {payload?.boundaryWarning && payload.markers.find((m) => m.kind === "hazard") && (
        <CircleMarker
          center={[
            payload.markers.find((m) => m.kind === "hazard")!.lat,
            payload.markers.find((m) => m.kind === "hazard")!.lon,
          ]}
          radius={18}
          pathOptions={{ color: "#f43f5e", fillColor: "#f43f5e", fillOpacity: 0.12, weight: 1.5, dashArray: "4 4" }}
        />
      )}
    </MapContainer>
  );
}
