/**
 * Proactive alerts for a saved location.
 *
 * Evaluates current conditions against the same deterministic rule engine used for queries,
 * and renders the notification EXACTLY as it would be delivered over SMS/IVR/push in
 * production. No SMS provider is integrated - this is a faithful preview of the payload,
 * not a send.
 */
import { runWeatherAgent } from "@/agents/weather";
import { runOceanAgent } from "@/agents/ocean";
import { runGeospatialAgent } from "@/agents/geospatial";
import { runRiskAgent } from "@/agents/risk";
import { HARBOURS } from "@/lib/layers";
import { harbourToLocation } from "@/lib/geo";
import { istHour, addHours, formatIST } from "@/lib/time";
import type { ResolvedLocation } from "@/lib/types";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** 160-character SMS body, the real constraint the production channel would impose. */
function smsBody(loc: string, band: string, rules: string[], score: number): string {
  const head = `ORCA ${band}: ${loc}`;
  const detail = rules.slice(0, 2).join("; ");
  const tail = `Risk ${score}/100. Do not rely solely on this msg.`;
  let msg = `${head}. ${detail}. ${tail}`;
  if (msg.length > 160) msg = `${head}. ${detail}.`.slice(0, 157) + "...";
  return msg;
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const harbourId = url.searchParams.get("harbour") ?? "kanyakumari";
  const h = HARBOURS.find((x) => x.id === harbourId) ?? HARBOURS[0];
  const location: ResolvedLocation = harbourToLocation(h);

  const now = istHour();
  const window = {
    label: "Next 12 hours",
    labelKey: "next12",
    startIST: now,
    endIST: addHours(now, 12),
    hoursAhead: 0,
  };

  const [ocean, weather] = await Promise.all([
    runOceanAgent(location, window, { timeoutMs: 6000 }),
    runWeatherAgent(location, window, { timeoutMs: 6000 }),
  ]);
  const geo = runGeospatialAgent(location);
  const risk = runRiskAgent({
    weather: weather.data,
    ocean: ocean.data,
    geo: geo.data,
    upstreamPenalty: ocean.confidencePenalty + weather.confidencePenalty,
    missing: [ocean, weather].filter((r) => !r.ok).map((r) => r.agent),
  });

  const r = risk.data!;
  const ruleLines = r.rules.map(
    (x) => `${x.label} (${x.measured} ${x.unit} vs ${x.comparator} ${x.threshold} ${x.unit})`,
  );

  return Response.json({
    location: { id: h.id, name: h.name, state: h.state, lat: h.lat, lon: h.lon },
    evaluatedAt: formatIST(now),
    window: window.label,
    risk: r,
    alerts: r.rules.map((x) => ({
      id: x.id,
      severity: x.severity,
      label: x.label,
      measured: `${x.measured} ${x.unit}`,
      threshold: `${x.comparator} ${x.threshold} ${x.unit}`,
      source: x.source,
    })),
    provenance: [...ocean.provenance, ...weather.provenance, ...geo.provenance],
    notificationPreview: {
      channelNote:
        "Preview only. This is the payload that production would deliver over SMS, IVR and push. No SMS provider is integrated in this prototype.",
      sms: smsBody(h.name, r.band, ruleLines, r.score),
      smsLength: smsBody(h.name, r.band, ruleLines, r.score).length,
      ivrScript:
        r.band === "SAFE"
          ? `This is ORCA. Conditions at ${h.name} are within normal limits for the next twelve hours. Press 1 to repeat.`
          : `This is ORCA. Warning for ${h.name}. ${ruleLines[0] ?? "Hazardous conditions"}. Risk level ${r.band}. Press 1 to repeat, press 2 for the nearest safe harbour.`,
      push: {
        title: `ORCA ${r.band} · ${h.name}`,
        body: ruleLines[0] ?? "Conditions within normal operating limits.",
      },
    },
  });
}
