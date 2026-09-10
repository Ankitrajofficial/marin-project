"""ORCA API.

Thin layer: routes read from the database and serialize. No hazard arithmetic
happens here -- every number was computed by core/ and stored, which is what
makes it reproducible and traceable.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (
    cells, chat, geofence, health, recall, risk, route, scenario,
)
from app.config import settings
from app.db import close_pool, open_pool

logging.basicConfig(level=settings.log_level,
                    format="%(levelname)-7s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Open the pool at startup so a bad DATABASE_URL is a boot failure with a
    # clear message, not a confusing error on the first request.
    await open_pool()
    yield
    await close_pool()


app = FastAPI(
    title="ORCA API",
    description=(
        "Marine hazard decision engine. Every number served here was computed "
        "by a deterministic solver in core/ and is traceable to the "
        "observations that produced it.\n\n"
        "**Simulated data**: any response carrying a hazard number includes a "
        "`simulated` flag. When true, the number came from injected scenario "
        "data and MUST NOT be presented as a real forecast.\n\n"
        "**Boundaries**: geofencing uses open datasets (MarineRegions/VLIZ, "
        "OpenStreetMap). They are ADVISORY ONLY, are not Survey of India "
        "definitions, and carry no legal authority. Every geofence response "
        "carries `advisory_only` and the required attributions."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(risk.router)
app.include_router(cells.router)
app.include_router(geofence.router)
app.include_router(chat.router)
app.include_router(recall.router)
app.include_router(scenario.router)
app.include_router(route.router)
