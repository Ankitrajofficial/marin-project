"use client";

import type { RiskFeatureCollection } from "@/lib/types";
import { verdictFor } from "@/lib/verdict";

/**
 * One plain-language line over the map, for the reader who will not open a
 * panel or read a legend: the fisherman deciding whether to launch.
 *
 * Display only. It reads the same RiskFeatureCollection the map paints and
 * calls no API of its own, so there is no second number to fall out of step
 * with the colours underneath it.
 */
export default function VerdictBanner({ risk }: { risk: RiskFeatureCollection | null }) {
  // Before the first response there is nothing to say. Deliberately renders
  // nothing rather than a placeholder -- an empty bar in the layout would be
  // read as a verdict of some kind, and "still loading" is not a verdict.
  if (!risk) return null;

  const v = verdictFor(risk);

  return (
    <div className={`verdict verdict-${v.level}`} role="status" aria-live="polite">
      {/* Prefix, not a separate line: the word SIMULATED has to travel with
          the verdict wherever it is quoted, screenshotted or read aloud. */}
      {v.simulated && <span className="verdict-sim">SIMULATED</span>}
      <span className="verdict-text">{v.text}</span>
    </div>
  );
}
