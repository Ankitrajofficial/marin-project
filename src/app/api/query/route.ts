/**
 * Streaming query endpoint.
 *
 * Returns Server-Sent Events so the reasoning trace fills in progressively on the client.
 * The orchestrator is an async generator; each event it yields is flushed immediately.
 */
import { orchestrate, type OrchestrationRequest } from "@/agents/orchestrator";
import type { Lang } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LANGS: Lang[] = ["en", "hi", "ta", "bn", "ml", "te"];

export async function POST(req: Request) {
  let body: Record<string, unknown> = {};
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return Response.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const query = typeof body.query === "string" ? body.query.trim() : "";
  if (!query) return Response.json({ error: "query is required" }, { status: 400 });
  if (query.length > 500) return Response.json({ error: "query too long" }, { status: 400 });

  const language = LANGS.includes(body.language as Lang) ? (body.language as Lang) : null;
  const device =
    body.device && typeof body.device === "object"
      ? (body.device as { lat: number; lon: number })
      : null;

  const request: OrchestrationRequest = {
    query,
    language,
    device: device && Number.isFinite(device.lat) && Number.isFinite(device.lon) ? device : null,
    scenarioId: typeof body.scenarioId === "string" ? body.scenarioId : null,
    useLlm: body.useLlm === false ? false : undefined,
    timeoutMs: typeof body.timeoutMs === "number" ? body.timeoutMs : undefined,
    uiPaceMs: typeof body.uiPaceMs === "number" ? body.uiPaceMs : undefined,
  };

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (obj: unknown) => {
        try {
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`));
        } catch {
          /* client disconnected mid-run; nothing to do */
        }
      };
      try {
        for await (const event of orchestrate(request)) send(event);
      } catch (err) {
        send({ type: "error", message: err instanceof Error ? err.message : String(err) });
        send({ type: "done", totalMs: 0, degradedAgents: ["orchestrator"] });
      } finally {
        try {
          controller.close();
        } catch {
          /* already closed */
        }
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
