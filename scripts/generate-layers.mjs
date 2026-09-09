/**
 * ORCA layer generator.
 *
 * Produces the CACHED geospatial layers shipped in /data. Run with `node scripts/generate-layers.mjs`.
 *
 * IMPORTANT — read this before believing anything about the PFZ layer:
 * The PFZ polygons this script emits are a DEMO DERIVATION. They are computed from a static
 * SST / chlorophyll proxy table using a simplified version of the thermal-front + productivity
 * logic that INCOIS uses. They are NOT INCOIS advisories and must never be labelled as such.
 * In production the geospatial agent consumes the real INCOIS PFZ advisory feed and this file
 * is deleted. See DECISIONS.md and README.md.
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = (name, obj) => {
  writeFileSync(join(ROOT, "data", name), JSON.stringify(obj, null, 1) + "\n");
  console.log("wrote data/" + name);
};

// Peninsular pseudo-centroid used to push points seaward.
const CX = 79.0, CY = 21.0;
const seaward = (lon, lat, deg) => {
  let ux = lon - CX, uy = lat - CY;
  const m = Math.hypot(ux, uy) || 1;
  ux /= m; uy /= m;
  return [ +(lon + (deg * ux) / Math.cos((lat * Math.PI) / 180)).toFixed(4), +(lat + deg * uy).toFixed(4) ];
};

// Coarse Indian mainland coastline, NW (Sir Creek) -> S (Kanyakumari) -> NE (Sundarbans).
// Hand-digitised at ~50 km resolution from public basemaps. Simplified on purpose: this is a
// demo cartographic layer, not a survey product.
const COAST = [
  [68.20,23.90],[68.98,22.47],[69.60,21.65],[70.37,20.90],[71.50,20.75],[72.65,21.55],
  [72.85,20.40],[72.83,19.20],[73.31,16.99],[73.68,15.85],[74.12,14.80],[74.70,13.35],
  [74.84,12.84],[75.20,12.00],[75.80,11.17],[76.26,9.93],[76.60,8.90],[77.10,8.35],
  [77.54,8.09],[78.13,8.76],[79.31,9.29],[79.30,9.90],[79.84,10.77],[79.84,11.93],
  [80.30,13.13],[80.15,14.30],[80.90,15.90],[81.15,16.30],[82.30,16.95],[83.22,17.69],
  [84.80,19.10],[86.68,20.26],[87.05,21.10],[87.51,21.63],[88.10,21.65],[88.90,21.60]
];

out("coastline.json", {
  type: "FeatureCollection",
  meta: { source: "ORCA simplified coastline (hand-digitised, ~50 km resolution)", kind: "CACHED", generated: new Date().toISOString().slice(0,10) },
  features: [{ type: "Feature", properties: { name: "Indian mainland coastline (simplified)", layer: "coastline" }, geometry: { type: "LineString", coordinates: COAST } }]
});

// EEZ band: coastline pushed 200 nm (~3.33 deg) seaward, closed back along the coast.
const EEZ_DEG = 3.33;
const offshore = COAST.map(([lon, lat]) => seaward(lon, lat, EEZ_DEG));
const eezRing = [...COAST, ...offshore.slice().reverse(), COAST[0]];
out("eez.json", {
  type: "FeatureCollection",
  meta: { source: "ORCA approximation: coastline offset 200 nm seaward", kind: "CACHED", generated: new Date().toISOString().slice(0,10),
          note: "Indicative EEZ extent for the demo map only. Not a legal boundary. Production uses the official EEZ shapefile." },
  features: [{ type: "Feature", properties: { name: "Indian EEZ (indicative 200 nm extent)", layer: "eez" }, geometry: { type: "Polygon", coordinates: [eezRing] } }]
});

/**
 * PFZ demo derivation.
 * Each anchor carries a static SST / chlorophyll-a proxy sample. We score a candidate zone on:
 *   frontStrength = |sstGradient| normalised (thermal fronts aggregate forage fish)
 *   productivity  = chlorophyll-a proxy normalised (primary production -> forage biomass)
 *   score = 0.45 * front + 0.55 * productivity
 * Zones scoring >= PFZ_EMIT_THRESHOLD are emitted. This mirrors the SHAPE of INCOIS PFZ logic
 * at toy fidelity - it is not INCOIS's algorithm and does not claim to be.
 */
const ANCHORS = [
  { id:"pfz-chennai",   near:"Chennai",        lon:80.30, lat:13.13, push:0.55, sst:28.9, sstGrad:0.42, chl:1.35 },
  { id:"pfz-pdy",       near:"Puducherry",     lon:79.84, lat:11.93, push:0.45, sst:29.2, sstGrad:0.31, chl:1.45 },
  { id:"pfz-nagai",     near:"Nagapattinam",   lon:79.84, lat:10.77, push:0.50, sst:29.4, sstGrad:0.30, chl:1.55 },
  { id:"pfz-rameswaram",near:"Rameswaram",     lon:79.31, lat:9.29,  push:0.35, sst:29.6, sstGrad:0.18, chl:0.78 },
  { id:"pfz-kanyakumari",near:"Kanyakumari",   lon:77.54, lat:8.09,  push:0.50, sst:28.4, sstGrad:0.55, chl:2.10 },
  { id:"pfz-vizhinjam", near:"Vizhinjam",      lon:76.99, lat:8.38,  push:0.45, sst:28.2, sstGrad:0.48, chl:1.90 },
  { id:"pfz-kochi",     near:"Kochi",          lon:76.26, lat:9.93,  push:0.55, sst:28.0, sstGrad:0.61, chl:2.60 },
  { id:"pfz-beypore",   near:"Beypore",        lon:75.80, lat:11.17, push:0.50, sst:28.3, sstGrad:0.44, chl:2.05 },
  { id:"pfz-mangaluru", near:"Mangaluru",      lon:74.84, lat:12.84, push:0.55, sst:28.6, sstGrad:0.35, chl:1.55 },
  { id:"pfz-ratnagiri", near:"Ratnagiri",      lon:73.31, lat:16.99, push:0.60, sst:28.8, sstGrad:0.34, chl:1.45 },
  { id:"pfz-veraval",   near:"Veraval",        lon:70.37, lat:20.90, push:0.65, sst:28.1, sstGrad:0.52, chl:2.40 },
  { id:"pfz-vizag",     near:"Visakhapatnam",  lon:83.22, lat:17.69, push:0.55, sst:29.0, sstGrad:0.38, chl:1.60 },
  { id:"pfz-paradip",   near:"Paradip",        lon:86.68, lat:20.26, push:0.60, sst:29.3, sstGrad:0.30, chl:1.50 },
  { id:"pfz-digha",     near:"Digha",          lon:87.51, lat:21.63, push:0.55, sst:29.5, sstGrad:0.28, chl:1.70 },
  { id:"pfz-tuticorin", near:"Thoothukudi",    lon:78.13, lat:8.76,  push:0.40, sst:29.1, sstGrad:0.37, chl:1.45 }
];

const PFZ_EMIT_THRESHOLD = 0.35;
const norm = (v, lo, hi) => Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
const pfzFeatures = [];
for (const a of ANCHORS) {
  const front = norm(a.sstGrad, 0.15, 0.65);
  const prod = norm(a.chl, 0.6, 2.8);
  const score = +(0.45 * front + 0.55 * prod).toFixed(3);
  if (score < PFZ_EMIT_THRESHOLD) continue;
  const [cx, cy] = seaward(a.lon, a.lat, a.push);
  const w = 0.30, h = 0.22;
  const ring = [[cx-w,cy-h],[cx+w,cy-h],[cx+w,cy+h],[cx-w,cy+h],[cx-w,cy-h]].map(([x,y]) => [+x.toFixed(4), +y.toFixed(4)]);
  pfzFeatures.push({
    type: "Feature",
    properties: {
      id: a.id, layer: "pfz", nearest_harbour: a.near,
      label: "Demo derivation - production consumes the INCOIS PFZ advisory feed",
      confidence: score >= 0.60 ? "high" : score >= 0.45 ? "moderate" : "low",
      score, sst_c: a.sst, sst_gradient_c_per_10km: a.sstGrad, chlorophyll_a_mg_m3: a.chl,
      derivation: "score = 0.45*norm(sst_gradient,0.15..0.65) + 0.55*norm(chlorophyll_a,0.6..2.8)",
      depth_band_m: "20-60", advisory_valid_hours: 24
    },
    geometry: { type: "Polygon", coordinates: [ring] }
  });
}
out("pfz-zones.json", {
  type: "FeatureCollection",
  meta: {
    source: "ORCA demo derivation from a static SST + chlorophyll-a proxy table",
    kind: "CACHED",
    generated: new Date().toISOString().slice(0,10),
    warning: "NOT an INCOIS advisory. Demo derivation only. Production consumes the INCOIS PFZ advisory feed.",
    derivation: "score = 0.45*norm(sst_gradient) + 0.55*norm(chlorophyll_a); emit zones with score >= 0.35"
  },
  features: pfzFeatures
});
console.log("pfz zones emitted:", pfzFeatures.length);
