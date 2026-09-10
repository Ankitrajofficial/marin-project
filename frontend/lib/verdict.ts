// Plain-language verdict for the hazard field.
//
// THE ONE PLACE the verdict word is decided. Everything here is a pure
// function of the SAME RiskFeatureCollection the map paints, so the banner and
// the colours cannot drift apart: no second fetch, no second threshold, no
// second notion of what "dangerous" means.
//
// No LLM. The words below are a deterministic threshold on hazard_prob, which
// core/risk.py already computed. This file adds no arithmetic to that number
// and never invents one -- it only chooses which word sits next to it.

import type { RiskFeatureCollection } from "./types";
import { fmtPct } from "./color";

// ---------------------------------------------------------------------------
// Thresholds.
//
// Deliberately equal to stops in HAZARD_STOPS rather than round numbers picked
// for the banner. 0.10 is where the scale leaves calm blue-grey, 0.50 is where
// it turns from orange to red. Tying the words to the same breakpoints as the
// colours means a viewer can never read CAUTION over a cell painted deep red,
// which is the kind of contradiction that destroys trust in the whole map.
//
// They are advisory bands for a plain-language reading, NOT a safety
// certification. The probability itself is the claim; the word is a label on
// it, which is why the number is always shown alongside.
export const CAUTION_AT = 0.10;   // >= 10% exceedance -> CAUTION
export const DO_NOT_GO_AT = 0.50; // >= 50% exceedance -> DO NOT GO OUT

// Above this, the spread across sources and lead time is wide enough that the
// point estimate should not be read as precise. It changes the WORDING only --
// never the verdict. Nudging a verdict up on low confidence would cry wolf;
// nudging it down would hide a real risk behind our own ignorance. Saying so
// plainly lets the reader apply their own judgement, which is the only honest
// option when the model's confidence is poor.
export const LOW_CONFIDENCE_AT = 0.60;

export type VerdictWord = "SAFE TO GO OUT" | "CAUTION" | "DO NOT GO OUT" | "NO DATA";

export interface Verdict {
  word: VerdictWord;
  /** Full line as displayed, number included. */
  text: string;
  /** Drives colour only. "none" for the no-data case, which must never be green. */
  level: "safe" | "caution" | "danger" | "unknown";
  simulated: boolean;
  lowConfidence: boolean;
}

/** Hours from now to the step being displayed. Negative values (a step already
 *  past) collapse to 0 rather than reading "in -1 hours". */
function leadHours(validTime: string | null): number | null {
  if (!validTime) return null;
  const t = new Date(validTime).getTime();
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((t - Date.now()) / 3_600_000));
}

function whenPhrase(validTime: string | null): string {
  const h = leadHours(validTime);
  if (h === null) return "";
  if (h === 0) return " now";
  if (h === 1) return " in 1 hour";
  return ` in ${h} hours`;
}

/**
 * Reduce the visible hazard field to one line a fisherman can act on.
 *
 * Driven by the MAXIMUM exceedance probability in view, not the mean. A mean
 * washes a dangerous patch out against calm water around it, and the boat only
 * has to cross the dangerous patch once.
 */
export function verdictFor(risk: RiskFeatureCollection | null): Verdict {
  const feats = risk?.features ?? [];

  // Cells that carry an actual number. hazard_prob === null means NO DATA --
  // core/risk.py is explicit that this is not zero risk, and the map already
  // paints it grey rather than calm.
  const withData = feats.filter((f) => f.properties.hazard_prob !== null);
  const noDataCount = feats.length - withData.length;

  // Any simulated cell in view taints the whole reading, matching the
  // collection-level guard the shell already applies.
  const simulated = Boolean(risk?.simulated) ||
    feats.some((f) => f.properties.simulated);

  // ---- no data ----------------------------------------------------------
  // The rule that matters most: grey must never read as SAFE. An empty map
  // over an area nobody has forecast is the state most easily mistaken for
  // "nothing to worry about", and that mistake puts a boat to sea.
  if (withData.length === 0) {
    return {
      word: "NO DATA",
      text: feats.length === 0
        ? "NO DATA FOR THIS AREA — no forecast cells here. This is not the same as safe."
        : `NO DATA FOR THIS AREA — ${feats.length} cells, none with a forecast. This is not the same as safe.`,
      level: "unknown",
      simulated,
      lowConfidence: false,
    };
  }

  // ---- the driving cell -------------------------------------------------
  const worst = withData.reduce((a, b) =>
    (b.properties.hazard_prob as number) > (a.properties.hazard_prob as number) ? b : a
  );
  const p = worst.properties.hazard_prob as number;

  // Confidence is read off the SAME cell the number comes from, not averaged
  // across the view: the claim being qualified is that cell's probability.
  const lowConfidence = worst.properties.uncertainty >= LOW_CONFIDENCE_AT;

  const word: VerdictWord =
    p >= DO_NOT_GO_AT ? "DO NOT GO OUT" : p >= CAUTION_AT ? "CAUTION" : "SAFE TO GO OUT";
  const level = word === "DO NOT GO OUT" ? "danger" : word === "CAUTION" ? "caution" : "safe";

  // The number is never optional. A bare "SAFE TO GO OUT" is an authority's
  // promise; "SAFE TO GO OUT — highest chance of dangerous seas is 0.4%" is a
  // measurement the reader can weigh, and disagree with.
  const when = whenPhrase(risk?.valid_time ?? null);
  const head =
    word === "SAFE TO GO OUT"
      ? `${word} — highest chance of dangerous seas is ${fmtPct(p)}${when}`
      : `${word} — ${fmtPct(p)} chance of dangerous seas${when}`;

  // Partial coverage is its own hazard. A green banner computed from the four
  // cells that happen to have data, over a view where forty do not, is a
  // confident answer about an area we have not looked at.
  const gaps = noDataCount > 0
    ? ` · no data for ${noDataCount} of ${feats.length} cells in view`
    : "";

  return {
    word,
    text: `${head}${gaps}${lowConfidence ? " · low confidence" : ""}`,
    level,
    simulated,
    lowConfidence,
  };
}

// ---------------------------------------------------------------------------
// Recall, in one line.
//
// "cannot reach shelter in time" is margin_h < 0 -- time to harbour exceeds
// time to hazard arrival. Same definition RecallPanel colours its rows by,
// kept here so the header and the list cannot disagree.
//
// margin_h === null is NOT one situation, and the difference decides whether a
// sentence about a boat's safety is true. core/recall.py leaves it null when:
//
//   a) no hazard crosses the vessel inside the computed horizon -- a real
//      answer, and good news; or
//   b) a hazard IS coming but time-to-harbour could not be computed, so no
//      margin exists.
//
// Folding (b) into the safe count would print "all 52 boats can reach shelter
// in time" while a storm closes on a boat whose transit we failed to solve.
// (b) is separated by the presence of a hazard time and reported as
// unassessed, never as safe.
export function recallSummary(
  ranked: Array<{ margin_h: number | null; time_to_hazard_h: number | null }>,
  cannotAssess: number,
  total: number
): string {
  const cannotReach = ranked.filter(
    (e) => e.margin_h !== null && e.margin_h < 0
  ).length;

  const unresolved = ranked.filter(
    (e) => e.margin_h === null && e.time_to_hazard_h !== null
  ).length;

  // Vessels the solver could not assess at all, plus case (b) above. Reported
  // separately and never folded into the safe count: "cannot assess" is not
  // "fine".
  const unknownCount = cannotAssess + unresolved;
  const unknown = unknownCount > 0 ? ` · ${unknownCount} cannot be assessed` : "";

  const boats = (n: number) => (n === 1 ? "boat" : "boats");

  if (total === 0) return "no vessels being tracked";

  // Every boat is unassessed. There is no safe count to lead with, and saying
  // "all N can reach shelter" here would be a claim about boats nobody looked
  // at. State the absence instead.
  if (unknownCount >= total) {
    return `none of the ${total} ${boats(total)} could be assessed`;
  }

  if (cannotReach > 0) {
    return `${cannotReach} of ${total} ${boats(total)} cannot reach shelter in time${unknown}`;
  }

  // The reassuring branch counts only boats actually assessed. Saying "all 52"
  // while three of them were unassessable claims the same boat is both fine
  // and unknown -- which is how a reader ends up trusting a number that covers
  // fewer boats than it names.
  const reached = total - unknownCount;
  if (unknownCount > 0) {
    return `${reached} of ${total} ${boats(total)} can reach shelter in time${unknown}`;
  }
  // "all 1 boat" is the sort of phrasing that makes a reader distrust the rest
  // of the sentence.
  return total === 1
    ? "the boat tracked can reach shelter in time"
    : `all ${total} boats can reach shelter in time`;
}
