"""Scenario control: activate, clear, status.

Deliberately its own router with its own prefix. Nothing here is reachable
from an ingest path, and every response says loudly whether a scenario is
running -- a simulated hazard field that is not obviously simulated is the
single worst failure this system could have.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_conn
from app.scenarios import runner

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scenario", tags=["scenario"])

WARNING = (
    "SIMULATED DATA IS ACTIVE. Every hazard value, risk cell, recall entry and "
    "chat answer derived from it is synthetic and is flagged simulated=true. "
    "It is a parametric idealisation for demonstration, NOT a forecast, and "
    "must not be used for any real decision."
)


class ScenarioStatus(BaseModel):
    active: bool
    scenario_id: str | None = None
    name: str | None = None
    activated_at: str | None = None
    params: dict[str, Any] | None = None
    counts: dict[str, Any] | None = None
    scenario_observations: int
    masked_real_observations: int
    simulated_risk_cells: int
    available: list[str]
    warning: str | None = None


class ActivateResult(BaseModel):
    scenario_id: str
    name: str
    start: str
    counts: dict[str, Any]
    #: Always true. Present so a client cannot render this without it.
    simulated: bool
    warning: str


class ClearResult(BaseModel):
    cleared: bool
    reason: str | None = None
    scenario_id: str | None = None
    name: str | None = None
    observations_removed: int = 0
    observations_restored: int = 0
    expected_restored: int | None = None
    #: False means the restore did not put back exactly what was masked --
    #: surfaced rather than swallowed, because it means real data is missing.
    restore_matches: bool = True
    masked_rows_left_behind: int = 0
    vessel_positions_removed: int = 0
    vessels_removed: int = 0
    #: risk_cells is derived, so clearing must purge it too -- a recompute
    #: alone cannot remove rows whose observations no longer exist.
    simulated_risk_cells_removed: int = 0
    orphan_risk_cells_removed: int = 0
    risk_cells_recomputed: int = 0


@router.get("/status", response_model=ScenarioStatus)
async def scenario_status() -> ScenarioStatus:
    async with get_conn() as conn:
        s = await runner.status(conn)
    return ScenarioStatus(**s, warning=WARNING if s["active"] else None)


@router.post("/{name}/activate", response_model=ActivateResult)
async def activate(name: str) -> ActivateResult:
    async with get_conn() as conn:
        try:
            r = await runner.activate(conn, name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except RuntimeError as e:
            raise HTTPException(409, str(e))
    return ActivateResult(**r, warning=WARNING)


@router.post("/clear", response_model=ClearResult)
async def clear() -> ClearResult:
    async with get_conn() as conn:
        return ClearResult(**await runner.clear(conn))
