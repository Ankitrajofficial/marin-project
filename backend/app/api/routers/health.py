"""GET /api/health -- is the database up, and how stale is each source."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from app.adapters.base import IssuedTimeKind
from app.api.schemas import Health, SourceHealth
from app.db import get_conn

router = APIRouter(prefix="/api", tags=["health"])

_SQL = """
    SELECT s.source_id, s.name, s.access_mode, s.reliability,
           count(o.*)                                   AS n_obs,
           max(o.issued_time)                           AS latest_issued,
           max(o.valid_time)                            AS latest_valid,
           coalesce(array_agg(DISTINCT o.issued_time_kind)
                    FILTER (WHERE o.issued_time_kind IS NOT NULL), '{}') AS kinds
      FROM sources s
      LEFT JOIN observations o ON o.source_id = s.source_id
     GROUP BY s.source_id, s.name, s.access_mode, s.reliability
     ORDER BY s.source_id
"""


@router.get("/health", response_model=Health)
async def health() -> Health:
    now = datetime.now(timezone.utc)
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1")
            db_ok = (await cur.fetchone())[0] == 1
            await cur.execute(_SQL)
            rows = await cur.fetchall()
            await cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE simulated) FROM risk_cells"
            )
            n_risk, n_sim = await cur.fetchone()

    sources = []
    for source_id, name, access_mode, reliability, n_obs, issued, valid, kinds in rows:
        sources.append(
            SourceHealth(
                source_id=source_id,
                name=name,
                access_mode=access_mode,
                reliability=float(reliability),
                n_observations=n_obs,
                latest_issued=issued,
                latest_valid=valid,
                issued_time_kinds=list(kinds),
                age_seconds=(now - issued).total_seconds() if issued else None,
                # A fetch-proxy age understates true staleness -- say so here
                # rather than letting a dashboard read it as gospel.
                age_is_lower_bound=IssuedTimeKind.FETCH_PROXY.value in kinds,
            )
        )

    return Health(
        status="ok" if db_ok else "degraded",
        database="up" if db_ok else "down",
        now=now,
        sources=sources,
        n_risk_cells=n_risk,
        n_simulated_risk_cells=n_sim,
    )
