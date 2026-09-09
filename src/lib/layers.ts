/**
 * Loads the CACHED geospatial layers shipped in /data and exposes them with their
 * provenance metadata attached. Every layer knows what it is and how honest it is about itself.
 */
import harboursRaw from "@data/harbours.json";
import pfzRaw from "@data/pfz-zones.json";
import imblRaw from "@data/imbl.json";
import mpaRaw from "@data/mpa.json";
import coastRaw from "@data/coastline.json";
import eezRaw from "@data/eez.json";

export interface Harbour {
  id: string;
  name: string;
  aliases: string[];
  state: string;
  coast: string;
  lat: number;
  lon: number;
  type: string;
}

export interface GeoFeature {
  type: "Feature";
  properties: Record<string, unknown>;
  geometry:
    | { type: "Polygon"; coordinates: number[][][] }
    | { type: "LineString"; coordinates: number[][] };
}

export interface GeoLayer {
  type: "FeatureCollection";
  meta: Record<string, unknown>;
  features: GeoFeature[];
}

export const HARBOURS: Harbour[] = (harboursRaw as { harbours: Harbour[] }).harbours;
export const HARBOURS_META = (harboursRaw as { meta: Record<string, unknown> }).meta;

export const PFZ = pfzRaw as unknown as GeoLayer;
export const IMBL = imblRaw as unknown as GeoLayer;
export const MPA = mpaRaw as unknown as GeoLayer;
export const COASTLINE = coastRaw as unknown as GeoLayer;
export const EEZ = eezRaw as unknown as GeoLayer;

/** Layer registry used by the map legend, the /api/layers route and the provenance chips. */
export const LAYER_REGISTRY = [
  { id: "pfz", label: "Potential Fishing Zones", data: PFZ, kind: "CACHED" as const },
  { id: "imbl", label: "Maritime boundaries (IMBL)", data: IMBL, kind: "CACHED" as const },
  { id: "mpa", label: "Marine protected areas", data: MPA, kind: "CACHED" as const },
  { id: "coastline", label: "Coastline", data: COASTLINE, kind: "CACHED" as const },
  { id: "eez", label: "EEZ (indicative)", data: EEZ, kind: "CACHED" as const },
];
