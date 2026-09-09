"""Enforcement of the one hard rule, after the fact.

CLAUDE.md: "The LLM never generates a number." A system prompt saying so is
guidance, not enforcement -- it fails silently and only under pressure, which
is exactly when it matters. This module checks the generated text against what
the tools actually returned, and rejects anything else.

WHAT THIS CATCHES: a fabricated, mis-transcribed, or mis-rounded figure, and
the crudest form of misleading framing (a risk adjective the numbers do not
support).

WHAT IT DOES NOT CATCH: a subtly wrong causal claim in prose, or a true number
presented in a misleading order. It is a floor, not a ceiling, and it should
not be described as more than that.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Matches integers and decimals, with optional thousands separators and a
# trailing percent sign.
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(%?)")

# Risk adjectives the model may not choose for itself. They assert a hazard
# LEVEL, which is a claim about the numbers, so they may only appear when the
# deterministic labeller in explain.py has already used them.
CONTROLLED_TERMS = (
    "safe", "unsafe", "dangerous", "danger", "hazardous", "severe", "extreme",
    "calm", "rough", "fine", "perfect", "no risk", "risk-free", "riskfree",
    "guaranteed", "certainly", "definitely", "always", "never",
)


@dataclass(slots=True)
class GuardReport:
    ok: bool
    unverified_numbers: list[str] = field(default_factory=list)
    uncontrolled_terms: list[str] = field(default_factory=list)

    def message(self) -> str:
        bits = []
        if self.unverified_numbers:
            bits.append(
                "These figures do not appear in any tool result: "
                + ", ".join(self.unverified_numbers)
                + ". Every number must be copied from a tool result."
            )
        if self.uncontrolled_terms:
            bits.append(
                "These risk words were not produced by the deterministic "
                "labeller: " + ", ".join(self.uncontrolled_terms)
                + ". Use only the wording in the findings."
            )
        return " ".join(bits)


def collect_allowed(obj: Any, values: set[float] | None = None,
                    literals: set[str] | None = None) -> tuple[set[float], set[str]]:
    """Walk a tool result and collect every number that may legitimately appear.

    Two collections, because numbers reach text two different ways:
      values   -- numeric leaves, which an answer may round or convert to a
                  percentage
      literals -- digit runs inside STRINGS (ISO timestamps, H3 cell ids,
                  labels like "next 48 hours"), which an answer quotes verbatim
    """
    values = set() if values is None else values
    literals = set() if literals is None else literals

    if isinstance(obj, bool):
        return values, literals
    if isinstance(obj, (int, float)):
        values.add(float(obj))
    elif isinstance(obj, str):
        literals.update(re.findall(r"\d+", obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            literals.update(re.findall(r"\d+", str(k)))
            collect_allowed(v, values, literals)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            collect_allowed(v, values, literals)
    return values, literals


def _verified(token_int: str, token_dec: str, is_pct: bool,
              values: set[float], literals: set[str]) -> bool:
    raw = token_int.replace(",", "")
    literal = raw + (f".{token_dec}" if token_dec else "")

    # Quoted verbatim from a string in the output (a timestamp, an id).
    if not token_dec and raw in literals:
        return True

    try:
        c = float(literal)
    except ValueError:
        return False

    dp = len(token_dec) if token_dec else 0
    for a in values:
        # As written, at the precision it was written to.
        if round(a, dp) == round(c, dp):
            return True
        # As a percentage of a probability. "0.2277" -> "23%" is a legitimate
        # restatement; the guard must not force robotic phrasing.
        if round(a * 100.0, dp) == round(c, dp):
            return True
        # A probability written back as a fraction of one.
        if abs(a) > 1 and round(a / 100.0, dp) == round(c, dp):
            return True
    return False


def check_numbers(text: str, tool_results: Any) -> list[str]:
    """Every number in `text` that is not derivable from `tool_results`."""
    values, literals = collect_allowed(tool_results)
    bad = []
    for m in _NUMBER.finditer(text):
        token_int, token_dec, pct = m.group(1), m.group(2), m.group(3)
        if not _verified(token_int, token_dec or "", bool(pct), values, literals):
            bad.append(m.group(0).strip())
    return bad


def check_terms(text: str, allowed_text: str) -> list[str]:
    """Risk adjectives used by the model that the deterministic findings did
    not use. `allowed_text` is the findings block explain.py produced.
    """
    low, allowed = text.lower(), allowed_text.lower()
    return [t for t in CONTROLLED_TERMS
            if re.search(rf"\b{re.escape(t)}\b", low)
            and not re.search(rf"\b{re.escape(t)}\b", allowed)]


def check(text: str, tool_results: Any, allowed_text: str) -> GuardReport:
    bad_numbers = check_numbers(text, tool_results)
    bad_terms = check_terms(text, allowed_text)
    return GuardReport(
        ok=not bad_numbers and not bad_terms,
        unverified_numbers=bad_numbers,
        uncontrolled_terms=bad_terms,
    )
