/**
 * Vessel recall triage endpoint.
 *
 * Given a cyclone hazard field and a fleet at sea, returns a PRIORITISED RECALL LIST — the
 * order in which a coastal authority should call boats in. This is the Disaster Management
 * core: not an advisory a fisherman reads, an operational triage a control room acts on.
 *
 * The fleet is SIMULATED (see src/lib/fleet.ts for why that is unavoidable and honest).
 * The cyclone track, the harbour set, the geodesy and the timing are all real.
 */
import { runTriageAgent, solveTriage } from "@/agents/triage";
import { loadDanaReplay } from "@/lib/cyclone";
import { generateFleet, fleetStats } from "@/lib/fleet";
import { runOceanAgent } from "@/agents/ocean";
import { harbourToLocation, distanceToCoastKm } from "@/lib/geo";
import { HARBOURS } from "@/lib/layers";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const size = Math.min(2000, Math.max(1, Number(url.searchParams.get("fleet") ?? 150)));
  const seed = Number(url.searchParams.get("seed") ?? 26176);
  const limit = Math.min(500, Math.max(1, Number(url.searchParams.get("limit") ?? 40)));

  // Only the Cyclone Dana replay is wired today. A live North Indian Ocean event would be
  // fetched from GDACS here; there is no active NIO cyclone at the time of writing, which is
  // the normal state and is why a labelled replay exists at all.
  const cyclone = loadDanaReplay();

  // Fleet on the coast the storm is actually threatening.
  const fleet = generateFleet({ size, seed, coasts: ["east"], minOffshoreNm: 4, maxOffshoreNm: 95 });

  /**
   * Decision time. A recall order is issued a day or two BEFORE landfall, not at landfall, so
   * the default is the first advisory in the track. `?at=` lets the demo walk the storm in and
   * show the list tighten. Clamped to the track window.
   */
  const requested = url.searchParams.get("at");
  const first = cyclone.track[0].at;
  const last = cyclone.track[cyclone.track.length - 1].at;
  const originIso =
    requested && Date.parse(requested) >= Date.parse(first) && Date.parse(requested) <= Date.parse(last)
      ? new Date(requested).toISOString()
      : first;

  /**
   * Landfall reference for the timing axis: the track advisory whose centre is closest to the
   * coastline. More robust than matching the GDACS current-position coordinate, and it is the
   * physically meaningful definition of landfall for a recall deadline.
   */
  const landfallPoint = cyclone.track.reduce(
    (best, p) => {
      const km = Math.abs(distanceToCoastKm(p.lon, p.lat));
      return km < best.km ? { at: p.at, km } : best;
    },
    { at: cyclone.track[0].at, km: Number.POSITIVE_INFINITY },
  );
  const landfallIso = landfallPoint.at;

  // Sea state at the DECISION time, not the storm peak, because that is what derates a
  // vessel's speed on the run home. Real archived Dana reanalysis for the replay.
  const paradip = HARBOURS.find((h) => h.id === "paradip")!;
  const decisionDay = originIso.slice(0, 10);
  const ocean = await runOceanAgent(
    harbourToLocation(paradip),
    { label: `${cyclone.eventname} @ ${decisionDay}`, labelKey: "", startIST: `${decisionDay}T00:00`, endIST: `${decisionDay}T23:00`, hoursAhead: 0 },
    {
      timeoutMs: 8000,
      scenarioId: "paradip-dana",
      archive: { startDate: decisionDay, endDate: decisionDay, label: `${cyclone.eventname} sea state at decision time` },
    },
  );
  const waveHeightM = ocean.data?.waveHeightM ?? 2.5;

  const agent = runTriageAgent({ fleet, cyclone, waveHeightM, originIso });
  const { results, summary } = agent.data!;

  /**
   * RECALL TIMING CURVE.
   *
   * The same fleet and the same storm, solved at every advisory timestamp in the track. It
   * answers the question a control room actually has: how much does it cost to decide late?
   *
   * Cheap to compute (the solver is a pure function) and it is the strongest single output of
   * this whole system, because it converts "issue the recall early" from advice into a number.
   */
  const curve = cyclone.track.map((p) => {
    const s = solveTriage({ fleet, cyclone, waveHeightM, originIso: p.at }).summary;
    const hoursBeforeLandfall = +(
      (Date.parse(landfallIso) - Date.parse(p.at)) / 3_600_000
    ).toFixed(1);
    return {
      at: p.at,
      label: p.label,
      hoursBeforeLandfall,
      counts: s.counts,
      unreachable: s.unreachable,
      diverted: s.diverted,
    };
  });

  return Response.json({
    summary,
    seaState: {
      waveHeightM,
      source: ocean.provenance[0]?.source ?? "unknown",
      kind: ocean.provenance[0]?.kind ?? "DERIVED",
      degraded: ocean.degraded,
      measuredAt: decisionDay,
      note: "Sea state is taken at the DECISION time, not the storm peak, because that is what derates a vessel's speed on the run home.",
    },
    cyclone: {
      eventname: cyclone.eventname,
      eventid: cyclone.eventid,
      alertlevel: cyclone.alertlevel,
      maxWindKmh: cyclone.maxWindKmh,
      severitytext: cyclone.severitytext,
      isReplay: cyclone.isReplay,
      upstreamSource: cyclone.upstreamSource,
      currentPosition: cyclone.currentPosition,
      track: cyclone.track,
      cone: cyclone.cone,
      note: cyclone.note,
      officialAuthority: {
        name: "RSMC New Delhi (India Meteorological Department)",
        url: "https://rsmcnewdelhi.imd.gov.in",
        label: "Official advisory — IMD RSMC New Delhi",
        note: "GDACS aggregates NOAA/NHC and JTWC advisories. It is not IMD. Always defer to the IMD bulletin.",
      },
      attribution: "Global Disaster Alert and Coordination System, GDACS",
    },
    fleetMeta: {
      ...fleetStats(fleet),
      simulated: true,
      why: "No public position feed exists for sub-20 m Indian fishing vessels; most carry no AIS. In production this list is driven by state fisheries registration, VHF check-in and the transponder rollout.",
      seed,
    },
    /** Truncated for transport; `summary.counts` reflects the whole fleet. */
    landfallIso,
    landfallDistanceToCoastKm: +landfallPoint.km.toFixed(1),
    recallTimingCurve: curve,
    diversionNote:
      summary.diverted === 0
        ? "No vessel benefits from diverting to a harbour other than its nearest. This is a real result, not a disabled feature: at 2-5 kn effective speed in these seas, a boat whose nearest harbour is inside the storm window cannot reach a farther one either. The decision is go-now-or-do-not-go, not choose-another-harbour."
        : `${summary.diverted} vessel(s) are directed away from their nearest harbour because the nearest is inside the storm window.`,
    recallList: results.slice(0, limit),
    truncated: results.length > limit,
    provenance: agent.provenance,
    toolCalls: agent.toolCalls,
  });
}
