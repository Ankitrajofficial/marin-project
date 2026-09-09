"""LangGraph orchestration: understand -> execute -> synthesize -> guard.

WHERE THE HARD RULE IS ENFORCED, node by node:

  understand   The LLM reads the question and returns SYMBOLS -- a tool name, a
               place NAME, a time SYMBOL. Never a coordinate, never a
               timestamp, never a measurement. It cannot: the structured output
               schema has no numeric fields except a buffer radius, which is
               range-validated downstream.
  resolve      Pure Python. places.py and timespec.py turn symbols into
               coordinates and UTC windows by lookup, not inference.
  execute      Only tools.py functions run. Every argument is Pydantic-
               validated before core/ sees it.
  synthesize   The LLM writes prose around findings that explain.py already
               built from the numbers. It is told it may not introduce a value.
  guard        Checks that it didn't. On failure it retries once with the
               violation named; on a second failure the deterministic findings
               are served verbatim. The system degrades to terse and correct,
               never to fluent and wrong.

STATE PERSISTENCE -- KNOWN LIMITATION: conversation state lives in LangGraph's
in-memory checkpointer, keyed by session_id, and is LOST WHEN THE API PROCESS
RESTARTS. The `traces` table is the durable record of what was asked and
answered. That is a deliberate demo-scale choice; production would use a
persistent checkpointer.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.agents import explain, guards, llm
from app.agents.places import known_places
from app.agents.timespec import TIME_SYMBOLS
from app.agents.tools import TOOL_NAMES, run_tool
from app.config import settings
from app.db import get_conn

log = logging.getLogger(__name__)

MAX_SYNTH_RETRIES = 1


# ===========================================================================
# What the LLM is allowed to emit
# ===========================================================================
class ToolCall(BaseModel):
    """One planned call. Note what is NOT here: no lat, no lon, no timestamp,
    no measurement. The model names a place and a time; Python resolves them.
    """

    tool: Literal["get_risk", "check_boundaries", "get_conditions"]
    place: str = Field(description="place name as the user said it, or 'lat,lon' "
                                   "if they gave coordinates")
    when: str | None = Field(default=None, description=f"one of: {', '.join(TIME_SYMBOLS)}")
    buffer_nm: float | None = Field(default=None, description="alert radius, NM")


class Plan(BaseModel):
    intent: Literal["risk", "boundaries", "conditions", "mixed", "unsupported"]
    reasoning: str = Field(description="one sentence on why these tools")
    calls: list[ToolCall] = Field(default_factory=list)
    clarification: str | None = Field(
        default=None,
        description="if the question cannot be answered with these tools, or a "
                    "place is missing, what to ask the user",
    )


_UNDERSTAND_SYSTEM = f"""You route questions about marine conditions on the \
Kerala–Tamil Nadu coast of India to a fixed set of tools.

You produce a PLAN ONLY. You never answer the question here.

Tools:
- get_risk: probability that waves or wind exceed danger thresholds at a place
  over a time window. Use for "is it safe to go out", "should I sail", "how
  rough will it be".
- check_boundaries: distance to maritime boundaries (the India–Sri Lanka IMBL,
  EEZ, territorial sea) and marine protected areas. Use for "which zones should
  I avoid", "how close am I to the border", "can I fish here".
- get_conditions: current or forecast wave height, wind speed, sea surface
  temperature at a place, with source and data age. Use for "what are the
  conditions", "how high are the waves".

Rules you must follow:
- NEVER output a latitude, longitude, timestamp, distance, speed or
  probability. You do not know any values. Name the place as the user said it
  and pick a time SYMBOL; the system resolves both.
- `when` must be exactly one of: {', '.join(TIME_SYMBOLS)}. If the user gives
  no time, use "now" for conditions and boundaries, "next_24h" for risk.
- Known places: {', '.join(known_places())}. Coordinates as "lat,lon" are also
  accepted. If the user names somewhere else, set intent="unsupported" and put
  the question to ask in `clarification`. Do NOT guess a nearby place.
- A follow-up that omits the place or time inherits it from the previous turn.
- If the question is not about marine conditions, boundaries or safety at sea,
  set intent="unsupported" and explain in `clarification`.
"""

_SYNTH_SYSTEM = """You are ORCA, a marine safety assistant for fishing crews \
and coastal authorities in India.

You will be given FINDINGS: sentences already written from verified computed
results. Your job is to turn them into a short, calm, direct answer.

ABSOLUTE RULES:
1. Every number in your answer must already appear in the findings. Do not
   compute, estimate, average, convert or infer any value. If a number is not
   in the findings, it does not exist.
2. Do not characterise the risk level in your own words. The findings contain
   the correct wording (for example "moderate probability"). Reuse it. Never
   write "safe", "dangerous", "calm", "fine", "severe" or similar unless that
   exact word is already in the findings.
3. State uncertainty and data age when the findings give them. Never round a
   number in a way that changes what it means.
4. If the findings say data is missing, say the data is missing. Missing data
   is never reported as an absence of danger.
5. If the findings carry an advisory-only boundary notice, include it.

Style: 2-5 sentences, plain English, no bullet lists, no preamble. Address the
reader directly. This is read by someone deciding whether to put to sea."""


class AgentState(TypedDict, total=False):
    session_id: str
    question: str
    history: list[dict[str, str]]
    plan: dict[str, Any]
    tool_results: list[dict[str, Any]]
    findings_text: str
    answer: str
    guard_feedback: str
    retries: int
    steps: list[dict[str, Any]]
    simulated: bool
    advisory_only: bool | None
    fell_back: bool


def _step(state: AgentState, **kw: Any) -> None:
    state.setdefault("steps", []).append(kw)


# ===========================================================================
# Nodes
# ===========================================================================
async def understand(state: AgentState) -> AgentState:
    t0 = time.perf_counter()
    history = state.get("history", [])
    convo = [{"role": h["role"], "content": h["content"]} for h in history[-6:]]
    convo.append({"role": "user", "content": state["question"]})

    # No provider name, no SDK, no sampling parameter. See agents/llm.py.
    plan, usage = await llm.plan(
        state["question"], Plan,
        system=_UNDERSTAND_SYSTEM,
        history=convo[:-1],
    )

    state["plan"] = plan.model_dump()
    _step(state, node="understand", model=settings.llm_model,
          input={"question": state["question"], "history_turns": len(history)},
          output=plan.model_dump(),
          duration_ms=round((time.perf_counter() - t0) * 1000),
          usage=usage)
    return state


async def execute(state: AgentState) -> AgentState:
    plan = state.get("plan") or {}
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    if plan.get("intent") == "unsupported" or not plan.get("calls"):
        state["tool_results"] = []
        state["findings_text"] = ""
        return state

    async with get_conn() as conn:
        for call in plan["calls"]:
            if call["tool"] not in TOOL_NAMES:
                continue
            args = {k: v for k, v in call.items()
                    if k != "tool" and v is not None}
            t0 = time.perf_counter()
            result = await run_tool(conn, call["tool"], args, now=now)
            results.append(result)
            _step(state, node="execute", tool=call["tool"], input=args,
                  output=result,
                  duration_ms=round((time.perf_counter() - t0) * 1000))

    state["tool_results"] = results
    state["findings_text"] = explain.findings_text(results)
    state["simulated"] = any(r.get("simulated") for r in results)
    adv = [r["advisory_only"] for r in results if "advisory_only" in r]
    # None, not False: "geofencing was not involved" is a different claim from
    # "these boundaries are authoritative".
    state["advisory_only"] = all(adv) if adv else None
    return state


async def synthesize(state: AgentState) -> AgentState:
    plan = state.get("plan") or {}

    # Nothing ran: answer from the plan's own clarification. Still no numbers.
    if not state.get("tool_results"):
        state["answer"] = plan.get("clarification") or (
            "I can only answer questions about sea conditions, hazard risk and "
            "maritime boundaries along the Kerala–Tamil Nadu coast."
        )
        state["fell_back"] = False
        return state

    t0 = time.perf_counter()
    context = ["FINDINGS:", state["findings_text"]]
    if state.get("guard_feedback"):
        context += ["", "Your previous attempt was REJECTED: "
                        + state["guard_feedback"], "Rewrite it."]

    text, usage = await llm.verbalize(
        f"Question: {state['question']}",
        "\n".join(context),
        system=_SYNTH_SYSTEM,
    )

    state["answer"] = text
    _step(state, node="synthesize", model=settings.llm_model,
          input={"findings": state["findings_text"],
                 "retry_feedback": state.get("guard_feedback")},
          output={"answer": text},
          duration_ms=round((time.perf_counter() - t0) * 1000),
          usage=usage)
    return state


async def guard(state: AgentState) -> AgentState:
    if not state.get("tool_results"):
        return state

    report = guards.check(state.get("answer", ""), state["tool_results"],
                          state.get("findings_text", ""))
    _step(state, node="guard", input={"answer": state.get("answer", "")},
          output={"ok": report.ok,
                  "unverified_numbers": report.unverified_numbers,
                  "uncontrolled_terms": report.uncontrolled_terms})

    if report.ok:
        state["guard_feedback"] = ""
        state["fell_back"] = False
        return state

    retries = state.get("retries", 0)
    if retries < MAX_SYNTH_RETRIES:
        state["retries"] = retries + 1
        state["guard_feedback"] = report.message()
        return state

    # Second failure: serve the deterministic findings. Terse and correct beats
    # fluent and wrong, every time.
    log.warning("guard failed twice; serving deterministic findings. %s",
                report.message())
    state["answer"] = (
        "Reporting the computed results directly:\n" + state["findings_text"]
    )
    state["fell_back"] = True
    # MUST clear: _after_guard routes back to synthesize while feedback is set.
    # Leaving it populated here loops synthesize->guard->synthesize forever,
    # burning tokens on a request the guard has already given up on.
    state["guard_feedback"] = ""
    _step(state, node="fallback",
          input={"reason": report.message()},
          output={"answer": state["answer"]})
    return state


def _after_guard(state: AgentState) -> str:
    """Retry only while there is feedback AND we have not already fallen back.

    Belt and braces: the fallback clears guard_feedback, and this also refuses
    to loop once fell_back is set. A routing function that can loop forever is
    worth two conditions.
    """
    if state.get("fell_back"):
        return END
    return "synthesize" if state.get("guard_feedback") else END


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("understand", understand)
    g.add_node("execute", execute)
    g.add_node("synthesize", synthesize)
    g.add_node("guard", guard)
    g.set_entry_point("understand")
    g.add_edge("understand", "execute")
    g.add_edge("execute", "synthesize")
    g.add_edge("synthesize", "guard")
    g.add_conditional_edges("guard", _after_guard,
                            {"synthesize": "synthesize", END: END})
    # In-memory only -- see the module docstring. traces is the durable record.
    return g.compile(checkpointer=MemorySaver())


GRAPH = build_graph()


async def ask(session_id: str, question: str) -> AgentState:
    """Run one turn. Conversation state is keyed by session_id."""
    config = {"configurable": {"thread_id": session_id}}
    prior = await GRAPH.aget_state(config)
    history = (prior.values.get("history") or []) if prior and prior.values else []

    state: AgentState = {
        "session_id": session_id, "question": question,
        "history": history, "steps": [], "retries": 0,
    }
    result = await GRAPH.ainvoke(state, config=config)
    result["history"] = history + [
        {"role": "user", "content": question},
        {"role": "assistant", "content": result.get("answer", "")},
    ]
    await GRAPH.aupdate_state(config, {"history": result["history"]})
    return result
