/** Serves the cached GeoJSON layers to the map, with their provenance metadata intact. */
import { PFZ, IMBL, MPA, COASTLINE, EEZ, HARBOURS, HARBOURS_META } from "@/lib/layers";

export const runtime = "nodejs";

export async function GET() {
  return Response.json(
    {
      pfz: PFZ,
      imbl: IMBL,
      mpa: MPA,
      coastline: COASTLINE,
      eez: EEZ,
      harbours: { meta: HARBOURS_META, items: HARBOURS },
    },
    { headers: { "cache-control": "public, max-age=3600" } },
  );
}
