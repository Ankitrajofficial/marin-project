/** Clears the in-memory response cache. Bound to the hidden demo reset shortcut. */
import { cacheClear, cacheStats } from "@/lib/cache";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST() {
  const before = cacheStats().entries;
  const cleared = cacheClear();
  return Response.json({ ok: true, entriesBefore: before, cleared });
}
