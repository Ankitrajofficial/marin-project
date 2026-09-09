/**
 * SYNTHESIS AGENT
 *
 * Turns every other agent's structured output into a natural-language answer, and attaches
 * the evidence list that backs each claim.
 *
 * Two implementations, same contract:
 *   template - deterministic sentence assembly from the six locale files. Zero config,
 *              always available, and the reason a missing API key cannot kill the demo.
 *   llm      - Claude rewrites the SAME facts into fluent prose in the query language.
 *
 * Critical constraint on the LLM path: the model is handed a closed set of already-computed
 * facts and is instructed to add nothing. It never sees raw APIs, never computes a risk band,
 * and the deterministic verdict is prepended to its output. If it were to drift, the risk
 * gauge beside it still shows the rule engine's answer.
 */
import { llmAvailable, llmComplete } from "@/lib/llm";
import { t } from "@/lib/i18n";
import { compass } from "@/lib/geo";
import { prov } from "@/lib/provenance";
import { ageLabel } from "@/lib/time";
import type {
  AgentName, AgentResult, AnswerPayload, ArchiveWindow, EvidenceItem, GeospatialData, Lang, OceanData,
  Provenance, QueryPlan, RiskData, RouteData, ToolCall, WeatherData,
} from "@/lib/types";

export interface SynthesisInput {
  plan: QueryPlan;
  weather: WeatherData | null;
  ocean: OceanData | null;
  geo: GeospatialData | null;
  risk: RiskData;
  route: RouteData | null;
  provenance: Provenance[];
  /** Provenance grouped by the agent that produced it, so evidence cites the right source. */
  agentProvenance: Partial<Record<AgentName, Provenance[]>>;
  missing: string[];
  archive?: ArchiveWindow;
  useLlm?: boolean;
}

/** Build the evidence list: every fact shown, with the agent that produced it. */
export function buildEvidence(input: SynthesisInput): EvidenceItem[] {
  const { weather: w, ocean: o, geo: g, risk, route } = input;
  const ev: EvidenceItem[] = [];
  /**
   * Attribute a fact to the provenance of the agent that produced it, optionally narrowing
   * to one source WITHIN that agent's list. Searching the flat list matches the wrong source
   * (e.g. "marine" also matches "marine protected area polygons"), so we never do that.
   */
  const pick = (agent: AgentName, needle?: string): Provenance => {
    const list = input.agentProvenance[agent] ?? [];
    if (needle) {
      const hit = list.find((p) => p.source.toLowerCase().includes(needle.toLowerCase()));
      if (hit) return hit;
    }
    return list[0] ?? prov("ORCA", "DERIVED", {});
  };

  if (o) {
    const p = pick("ocean");
    ev.push({ fact: "Significant wave height", value: `${o.waveHeightM} m`, agent: "ocean", provenance: p });
    ev.push({ fact: "Swell height", value: `${o.swellHeightM} m`, agent: "ocean", provenance: p });
    ev.push({ fact: "Wave period", value: `${o.wavePeriodS} s`, agent: "ocean", provenance: p });
    ev.push({ fact: "Sea surface temperature", value: `${o.seaSurfaceTempC} °C`, agent: "ocean", provenance: p });
  }
  if (w) {
    const p = pick("weather");
    ev.push({ fact: "Sustained wind", value: `${w.windSpeedKmh} km/h`, agent: "weather", provenance: p });
    ev.push({ fact: "Wind gusts", value: `${w.windGustKmh} km/h`, agent: "weather", provenance: p });
    ev.push({ fact: "Rainfall in window", value: `${w.precipitationMm} mm`, agent: "weather", provenance: p });
    ev.push({ fact: "Conditions", value: w.weatherLabel, agent: "weather", provenance: p });
  }
  if (g?.nearestPfz) {
    ev.push({
      fact: "Nearest potential fishing zone",
      value: `${g.nearestPfz.distanceKm} km ${compass(g.nearestPfz.bearingDeg)}`,
      agent: "geospatial",
      provenance: pick("geospatial", "pfz"),
    });
  }
  if (g?.boundary) {
    ev.push({
      fact: `Distance to ${g.boundary.name}`,
      value: `${g.boundary.distanceNm} nm`,
      agent: "geospatial",
      provenance: pick("geospatial", "imbl"),
    });
  }
  if (g?.mpa) {
    ev.push({
      fact: g.mpa.inside ? `Inside ${g.mpa.name}` : `Nearest protected area (${g.mpa.name})`,
      value: g.mpa.inside ? "yes" : `${g.mpa.distanceKm} km`,
      agent: "geospatial",
      provenance: pick("geospatial", "protected"),
    });
  }
  if (route) {
    ev.push({
      fact: "Route distance",
      value: `${route.distanceKm} km over ${route.waypoints.length} waypoints`,
      agent: "route",
      provenance: pick("route"),
    });
  }
  ev.push({
    fact: "Risk score",
    value: `${risk.score}/100 (${risk.band})`,
    agent: "risk",
    provenance: prov(`ORCA deterministic rule engine ${risk.engine}`, "DERIVED", {
      note: "No language model involved in this value.",
    }),
  });
  return ev;
}

/** Deterministic answer assembly. Always available, in all six languages. */
export function templateAnswer(input: SynthesisInput): string {
  const { plan, weather: w, ocean: o, geo: g, risk, route } = input;
  const L: Lang = plan.language;
  const parts: string[] = [];
  const loc = plan.location.name;
  // A historical replay carries its event name as the label and has no locale key.
  const windowLabel = plan.timeWindow.labelKey
    ? t(L, `window.${plan.timeWindow.labelKey}`)
    : plan.timeWindow.label;

  if (input.archive) parts.push(t(L, "ans.replay", { event: input.archive.label }));

  if (plan.intent === "unknown") parts.push(t(L, "ans.unknown"));

  if (plan.location.inland) {
    parts.push(t(L, "ans.inland", {
      km: g?.distanceToCoastKm ?? "?",
      name: g?.nearestHarbour?.name ?? "-",
    }));
  }

  // Verdict first for anything safety-shaped, then a standalone scope sentence.
  if (["safety", "alerts", "conditions", "route", "geofence"].includes(plan.intent)) {
    parts.push(t(L, `ans.verdict.${risk.band}`));
  }
  parts.push(t(L, "ans.at", { loc, window: windowLabel }));

  // A productivity question is asking WHY, so lead with the explanation, not the zone.
  if (plan.intent === "productivity" && g?.nearestPfz && o) {
    parts.push(t(L, "ans.productivity", { loc, sst: o.seaSurfaceTempC, score: g.nearestPfz.score }));
    parts.push(t(L, "ans.productivityNote"));
  }

  if (plan.intent === "pfz" || plan.intent === "productivity") {
    if (g?.nearestPfz) {
      parts.push(t(L, "ans.pfzFound", {
        km: g.nearestPfz.distanceKm,
        dir: compass(g.nearestPfz.bearingDeg),
        loc,
        depth: g.nearestPfz.depthBandM,
        sst: g.nearestPfz.sstC,
        chl: g.nearestPfz.chlorophyll,
      }));
      parts.push(t(L, "ans.pfzNote"));
    } else {
      parts.push(t(L, "ans.pfzNone"));
    }
  }

  if (o) {
    parts.push(t(L, "ans.sea", {
      wave: o.waveHeightM, swell: o.swellHeightM, period: o.wavePeriodS, sst: o.seaSurfaceTempC,
    }));
  }
  if (w) {
    // WMO condition names are localised; fall back to the English label for rare codes.
    const wmoKey = `wmo.${w.weatherCode}`;
    const localisedCondition = t(L, wmoKey) === wmoKey ? w.weatherLabel : t(L, wmoKey);
    parts.push(t(L, "ans.wind", {
      wind: w.windSpeedKmh, gust: w.windGustKmh, dir: compass(w.windDirectionDeg),
      weather: localisedCondition, precip: w.precipitationMm,
    }));
  }

  // Boundary proximity always gets said when it matters, whatever the intent.
  if (g?.boundary) {
    const b = g.boundary;
    const key =
      b.severity === "critical" ? "ans.geoCritical" :
      b.severity === "warning" ? "ans.geoWarning" :
      b.severity === "advisory" ? "ans.geoAdvisory" : "ans.geoClear";
    if (b.severity !== "clear" || plan.intent === "geofence") {
      parts.push(t(L, key, { nm: b.distanceNm, name: b.name }));
      parts.push(t(L, "ans.geoAccuracy"));
    }
  }
  if (g?.mpa?.inside) {
    parts.push(t(L, "ans.mpaInside", { name: g.mpa.name, designation: g.mpa.designation, rule: g.mpa.rule }));
  }

  if (route) {
    parts.push(t(L, "ans.route", {
      from: route.from.name, to: route.to.name, km: route.distanceKm,
      n: route.waypoints.length, band: t(L, `risk.band.${route.worstBand}`), mean: route.meanRisk,
    }));
    parts.push(route.detoursForced.length
      ? t(L, "ans.routeDetour", { list: route.detoursForced.slice(0, 3).join(" | ") })
      : t(L, "ans.routeClear"));
  }

  if (g?.nearestHarbour && ["safety", "alerts", "route"].includes(plan.intent)) {
    parts.push(t(L, "ans.harbour", { name: g.nearestHarbour.name, km: g.nearestHarbour.distanceKm }));
  }

  parts.push(risk.rules.length
    ? t(L, "ans.rules", {
        list: risk.rules
          .map((r) => {
            const key = `rules.${r.id}`;
            const localised = t(L, key, r.labelVars ?? {});
            const label = localised === key ? r.label : localised;
            return `${label} (${r.measured} ${r.unit} vs ${r.comparator} ${r.threshold} ${r.unit})`;
          })
          .join("; "),
      })
    : t(L, "ans.noRules"));

  if (input.missing.length) parts.push(t(L, "ans.degraded", { list: input.missing.join(", ") }));

  const sources = [...new Set(input.provenance.map((p) => `${p.source} [${p.kind}, ${ageLabel(p.ageSeconds)}]`))];
  parts.push(t(L, "ans.sources", { list: sources.join("; ") }));
  parts.push(t(L, "ans.engineNote"));

  return parts.filter(Boolean).join(" ");
}

const SYNTH_SYSTEM = `You are the SYNTHESIS agent for ORCA, a marine advisory system used by Indian fishermen and coastal officers.

You will receive a JSON block of facts that OTHER agents have already computed. Write the answer for the user.

HARD RULES:
1. Use ONLY the facts in the JSON. Never invent, estimate or round a number that is not there.
2. NEVER contradict "riskBand" or "riskScore". Those come from a deterministic rule engine, not from you. State the band as given.
3. Write in the language given by "language" (en=English, hi=Hindi, ta=Tamil, bn=Bengali, ml=Malayalam, te=Telugu). Write ONLY in that language.
4. Lead with the practical answer, then the numbers that justify it, then the caveats.
5. If "pfzIsDemoDerivation" is true you MUST say the fishing zone is a demo derivation and NOT an INCOIS advisory.
6. If "archiveReplay" is present you MUST say this is a historical replay, not current conditions.
7. Speak plainly to someone deciding whether to take a small boat out. 90-140 words. No markdown, no headings, no bullet points.`;

export async function runSynthesisAgent(input: SynthesisInput): Promise<AgentResult<AnswerPayload>> {
  const t0 = Date.now();
  const startedAt = new Date().toISOString();
  const toolCalls: ToolCall[] = [];
  const evidence = buildEvidence(input);
  const { plan, risk } = input;

  let text = templateAnswer(input);
  let mode: "llm" | "template" = "template";

  const wantLlm = input.useLlm !== false && llmAvailable();

  if (wantLlm) {
    const facts = {
      language: plan.language,
      intent: plan.intent,
      location: plan.location.name,
      timeWindow: plan.timeWindow.label,
      riskScore: risk.score,
      riskBand: risk.band,
      triggeredRules: risk.rules.map((r) => ({
        rule: r.label, measured: `${r.measured} ${r.unit}`, threshold: `${r.comparator} ${r.threshold} ${r.unit}`, source: r.source,
      })),
      sea: input.ocean && {
        waveHeightM: input.ocean.waveHeightM, swellM: input.ocean.swellHeightM,
        wavePeriodS: input.ocean.wavePeriodS, sstC: input.ocean.seaSurfaceTempC,
      },
      weather: input.weather && {
        windKmh: input.weather.windSpeedKmh, gustKmh: input.weather.windGustKmh,
        rainMm: input.weather.precipitationMm, conditions: input.weather.weatherLabel,
      },
      nearestFishingZone: input.geo?.nearestPfz && {
        distanceKm: input.geo.nearestPfz.distanceKm,
        bearing: compass(input.geo.nearestPfz.bearingDeg),
        sstC: input.geo.nearestPfz.sstC, chlorophyll: input.geo.nearestPfz.chlorophyll,
        depthBandM: input.geo.nearestPfz.depthBandM,
      },
      pfzIsDemoDerivation: Boolean(input.geo?.nearestPfz),
      nearestBoundary: input.geo?.boundary && {
        name: input.geo.boundary.name, distanceNm: input.geo.boundary.distanceNm,
        severity: input.geo.boundary.severity, positionsAreApproximate: true,
      },
      insideProtectedArea: input.geo?.mpa?.inside ? input.geo.mpa : null,
      nearestHarbour: input.geo?.nearestHarbour,
      route: input.route && {
        from: input.route.from.name, to: input.route.to.name, km: input.route.distanceKm,
        waypoints: input.route.waypoints.length, worstBand: input.route.worstBand, meanRisk: input.route.meanRisk,
      },
      archiveReplay: input.archive?.label ?? null,
      missingData: input.missing,
    };

    const res = await llmComplete(SYNTH_SYSTEM, JSON.stringify(facts, null, 1), {
      maxTokens: 700, timeoutMs: 15000, temperature: 0.3,
    });
    toolCalls.push({
      tool: "anthropic.messages.create (answer synthesis)",
      params: { model: "claude-sonnet-5", language: plan.language, factsOnly: true },
      status: res.ok ? "ok" : "error",
      durationMs: res.durationMs,
      summary: res.ok
        ? `Generated ${res.text.trim().split(/\s+/).length} words in "${plan.language}" from ${Object.keys(facts).length} pre-computed fact groups`
        : `${res.error} - falling back to the template generator`,
      raw: { factsGivenToModel: facts, response: res.ok ? res.text : undefined, error: res.error },
    });
    if (res.ok && res.text.trim().length > 40) {
      text = res.text.trim();
      mode = "llm";
    }
  } else {
    toolCalls.push({
      tool: "template.compose",
      status: "fallback",
      durationMs: 1,
      summary: `Deterministic template synthesis in "${plan.language}" (${llmAvailable() ? "LLM disabled for this run" : "no ANTHROPIC_API_KEY"})`,
      raw: { language: plan.language, intent: plan.intent, sentences: text.split(". ").length },
    });
  }

  const answer: AnswerPayload = {
    text, language: plan.language, mode, evidence,
    confidence: risk.confidence, band: risk.band,
  };

  return {
    agent: "synthesis",
    task: "Compose the answer, citing which agent supplied which fact",
    ok: true,
    degraded: false,
    data: answer,
    confidencePenalty: 0,
    provenance: [
      prov(mode === "llm" ? "Claude (claude-sonnet-5) answer synthesis" : "ORCA template synthesis", "DERIVED", {
        note: mode === "llm"
          ? "The model rewrote pre-computed facts into prose. It was given no raw data and no ability to change the risk band."
          : "Deterministic sentence assembly from the bundled locale files. No network, no API key.",
      }),
    ],
    toolCalls,
    durationMs: Date.now() - t0,
    startedAt,
  };
}
