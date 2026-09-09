"""Numbers -> plain language, deterministically.

The load-bearing sentences are built HERE, in Python, from tool output. The
LLM's only job at synthesis time is connective prose around them, and
guards.py checks it did not smuggle anything in.

This is why the risk LABEL is computed rather than chosen: "moderate" versus
"dangerous" is a claim about the number, and letting a language model pick
between them is exactly the failure the hard rule exists to prevent.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Probability bands. A CONVENTION, documented and applied consistently, not a
# calibrated finding -- which is why the number is always stated alongside the
# label and never replaced by it.
BANDS: tuple[tuple[float, str], ...] = (
    (0.02, "very low probability"),
    (0.10, "low probability"),
    (0.25, "moderate probability"),
    (0.50, "elevated probability"),
    (1.01, "high probability"),
)


def risk_label(p: float | None) -> str:
    if p is None:
        return "unknown — no data"
    for upper, label in BANDS:
        if p < upper:
            return label
    return "high probability"


def pct(p: float | None) -> str:
    return "unknown" if p is None else f"{p * 100:.1f}%"


def _age(entry: dict[str, Any]) -> str:
    mins = entry.get("age_minutes")
    if mins is None:
        return "age unknown"
    txt = f"{mins:.0f} min" if mins < 90 else f"{mins / 60:.1f} h"
    # "at least" because a fetch-proxy timestamp is when ORCA retrieved the
    # value, not when the model produced it.
    return f"at least {txt} old" if entry.get("age_is_lower_bound") else f"{txt} old"


def sources_line(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "Sources: none."
    bits = [f"{s['source_id']} ({', '.join(s['variables'])}, {_age(s)})"
            for s in sources]
    return "Sources: " + "; ".join(bits) + "."


def findings_for_risk(r: dict[str, Any]) -> list[str]:
    """Deterministic sentences for a get_risk result."""
    if r.get("no_data"):
        return [f"No hazard field has been computed for {r['place']['name']} "
                f"in {r['window']['label']}. That is missing data, not an "
                f"absence of hazard."]

    out = []
    mx = r.get("max_hazard_prob")
    out.append(
        f"For {r['place']['name']} during {r['window']['label']}, the highest "
        f"hazard probability across {r['n_steps']} hourly steps is {pct(mx)} "
        f"({risk_label(mx)})."
    )
    if r.get("peak_time"):
        out.append(f"The peak is at {r['peak_time']}.")

    th = r.get("thresholds", {})
    parts = [f"{k} above {v['threshold']} {v['unit']}" for k, v in th.items()]
    if parts:
        out.append("That is the probability of " + " or ".join(parts) + ".")

    if not r.get("cell_is_exact", True):
        out.append(
            f"No hazard field was computed for the exact position; the nearest "
            f"computed cell is {r['cell_rings_from_place']} cell(s) away."
        )
    if r.get("any_partial_coverage"):
        out.append("Some hours are missing a hazard variable, so those values "
                   "are a lower bound.")
    mu = r.get("max_uncertainty")
    if mu is not None:
        out.append(f"Uncertainty on these figures reaches {pct(mu)}; the spread "
                   f"is estimated from documented constants, not a forecast "
                   f"ensemble.")
    out.append(sources_line(r.get("sources", [])))
    return out


def findings_for_boundaries(r: dict[str, Any]) -> list[str]:
    out = [f"Position check for {r['place']['name']} with a "
           f"{r['buffer_nm']} NM buffer: {r['verdict']}."]
    for h in r.get("inside", []):
        out.append(f"Inside {h['name']} ({h['zone_type']}); its edge is "
                   f"{h['distance_nm']} NM away.")
    for h in r.get("alerts", []):
        if h.get("inside"):
            continue
        out.append(
            f"{h['name']} ({h['zone_type']}) is {h['distance_nm']} NM away, "
            f"bearing {h['bearing_deg']}°; after a {h['margin_nm']} NM "
            f"uncertainty margin that counts as {h['effective_distance_nm']} NM."
        )
    if not r.get("alerts"):
        out.append("No zone is within the buffer once the uncertainty margin "
                   "is applied.")
    if r.get("advisory_only"):
        out.append("These boundaries are open data and ADVISORY ONLY. They are "
                   "not Survey of India definitions and carry no legal "
                   "authority; do not use them for navigation or enforcement.")
    return out


def findings_for_conditions(r: dict[str, Any]) -> list[str]:
    if r.get("no_data"):
        return [f"No observations are stored for {r['place']['name']} in "
                f"{r['window']['label']}."]
    out = [f"Conditions at {r['place']['name']}, {r['window']['label']}:"]
    for o in r["observations"]:
        borrowed = "" if o["origin"] == "self" else " (from the adjacent cell)"
        out.append(
            f"{o['variable']} {o['value']} {o['unit']} at {o['valid_time']}, "
            f"from {o['source_id']}, {_age(o)}{borrowed}."
        )
    return out


_BY_TOOL = {
    "get_risk": findings_for_risk,
    "check_boundaries": findings_for_boundaries,
    "get_conditions": findings_for_conditions,
}


def findings(tool_results: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for r in tool_results:
        if r.get("error"):
            out.append(f"{r['tool']} could not run: {r.get('message', r['error'])}")
            continue
        fn = _BY_TOOL.get(r.get("tool", ""))
        if fn:
            out.extend(fn(r))
    return out


def findings_text(tool_results: list[dict[str, Any]]) -> str:
    return "\n".join(f"- {line}" for line in findings(tool_results))
