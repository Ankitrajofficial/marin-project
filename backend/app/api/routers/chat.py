"""POST /api/chat -- the conversational layer.

Every response carries the trace, the sources with their data age, the
simulated flag, and advisory_only when geofencing was involved. All four are
required fields: an endpoint cannot serve a number without saying where it
came from and how old it is.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents.graph import ask, ask_streaming
from app.agents.llm import LLMError
from app.db import get_conn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None


class ChatSource(BaseModel):
    source_id: str
    variables: list[str] = Field(default_factory=list)
    age_minutes: float | None = None
    #: True when the source publishes no model run time, so the age is a lower
    #: bound on true staleness.
    age_is_lower_bound: bool = False
    issued_time_kind: str | None = None
    reliability: float | None = None
    simulated: bool = False


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    #: The plan the model produced, and every tool call with its validated
    #: input and full output. Required -- the trace is not optional garnish,
    #: it is how a number is checkable.
    trace: list[dict[str, Any]]
    plan: dict[str, Any] | None
    findings: str
    sources: list[ChatSource]
    #: Required. True if any number came from scenario data.
    simulated: bool
    #: None means geofencing was not involved -- NOT the same as claiming an
    #: authoritative boundary.
    advisory_only: bool | None
    #: True when the guard rejected the model's prose twice and the
    #: deterministic findings were served instead.
    fell_back: bool
    highlights: dict[str, list[str]]


_INSERT_TRACE = """
INSERT INTO traces (session_id, user_query, steps, final_answer,
                    simulated, advisory_only)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING trace_id
"""


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or str(uuid.uuid4())

    try:
        state = await ask(session_id, req.message)
    except LLMError as e:
        # A rate limit, a timeout, or a missing key. Surface the sentence
        # written for a human -- never a partial or invented answer, and never
        # a hang: llm.py enforces a total wall-clock deadline.
        raise HTTPException(503, e.user_message)

    results = state.get("tool_results", []) or []

    sources: dict[str, ChatSource] = {}
    cells, zones = [], []
    for r in results:
        for s in r.get("sources", []):
            sources.setdefault(s["source_id"], ChatSource(**{
                k: v for k, v in s.items() if k in ChatSource.model_fields
            }))
        if r.get("h3_cell"):
            cells.append(r["h3_cell"])
        for h in (r.get("alerts", []) + r.get("inside", [])):
            zones.append(h["zone_id"])

    simulated = bool(state.get("simulated"))
    advisory_only = state.get("advisory_only")

    # Persist BEFORE returning: the trace is the durable record, and an answer
    # that was shown but not recorded is an answer nobody can audit.
    from psycopg.types.json import Json
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(_INSERT_TRACE, (
                session_id, req.message, Json(state.get("steps", [])),
                state.get("answer", ""), simulated, advisory_only,
            ))
            trace_id = (await cur.fetchone())[0]

    log.info("chat trace_id=%s session=%s tools=%d", trace_id, session_id,
             len(results))

    return ChatResponse(
        session_id=session_id,
        answer=state.get("answer", ""),
        trace=state.get("steps", []),
        plan=state.get("plan"),
        findings=state.get("findings_text", ""),
        sources=sorted(sources.values(), key=lambda s: s.source_id),
        simulated=simulated,
        advisory_only=advisory_only,
        fell_back=bool(state.get("fell_back")),
        highlights={"cells": sorted(set(cells)), "zones": sorted(set(zones))},
    )


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """Server-sent events: phases while the deterministic work runs, then the
    GUARDED answer streamed out.

    Nothing unverified is ever emitted -- synthesis is buffered and guarded
    before the first character reaches the client. See agents/graph.ask_streaming
    for why retraction was rejected.
    """
    session_id = req.session_id or str(uuid.uuid4())

    async def gen():
        yield _sse("open", {"session_id": session_id})
        state: dict[str, Any] = {}
        try:
            async for ev in ask_streaming(session_id, req.message):
                kind = ev.pop("type")
                if kind == "done":
                    state = ev["state"]
                    continue
                yield _sse(kind, ev)
        except LLMError as e:
            # The user-facing sentence, never a stack trace and never a
            # partial answer presented as complete.
            yield _sse("error", {"message": e.user_message})
            return
        except Exception as e:                       # pragma: no cover
            log.exception("chat stream failed")
            yield _sse("error", {"message": f"Internal error: {type(e).__name__}"})
            return

        results = state.get("tool_results", []) or []
        sources: dict[str, ChatSource] = {}
        cells, zones = [], []
        for r in results:
            for sc in r.get("sources", []):
                sources.setdefault(sc["source_id"], ChatSource(**{
                    k: v for k, v in sc.items() if k in ChatSource.model_fields}))
            if r.get("h3_cell"):
                cells.append(r["h3_cell"])
            for h in (r.get("alerts", []) + r.get("inside", [])):
                zones.append(h["zone_id"])

        simulated = bool(state.get("simulated"))
        advisory_only = state.get("advisory_only")

        # Persist before finishing: an answer that was shown but not recorded
        # is an answer nobody can audit.
        from psycopg.types.json import Json
        async with get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_INSERT_TRACE, (
                    session_id, req.message, Json(state.get("steps", [])),
                    state.get("answer", ""), simulated, advisory_only,
                ))
                trace_id = (await cur.fetchone())[0]

        yield _sse("done", {
            "session_id": session_id,
            "trace_id": trace_id,
            "answer": state.get("answer", ""),
            "trace": state.get("steps", []),
            "plan": state.get("plan"),
            "findings": state.get("findings_text", ""),
            "sources": [s.model_dump() for s in
                        sorted(sources.values(), key=lambda x: x.source_id)],
            "simulated": simulated,
            "advisory_only": advisory_only,
            "fell_back": bool(state.get("fell_back")),
            "highlights": {"cells": sorted(set(cells)), "zones": sorted(set(zones))},
        })

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",   # stop any proxy buffering the stream
    })
