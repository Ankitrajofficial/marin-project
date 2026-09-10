"""Scenario control from the command line.

    python -m jobs.scenario status
    python -m jobs.scenario activate bay_of_bengal_cyclone
    python -m jobs.scenario clear

A SEPARATE entry point from every ingest job on purpose: no ingest path can
reach the scenario package, and activating one always takes an explicit name.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from app.config import settings
from app.db import close_pool, get_conn, open_pool
from app.scenarios import runner


async def run(action: str, name: str | None) -> int:
    await open_pool()
    try:
        async with get_conn() as conn:
            if action == "status":
                print(json.dumps(await runner.status(conn), indent=2, default=str))
            elif action == "activate":
                if not name:
                    print("activate needs a scenario name", file=sys.stderr)
                    return 2
                print(json.dumps(await runner.activate(conn, name), indent=2,
                                 default=str))
            elif action == "clear":
                print(json.dumps(await runner.clear(conn), indent=2, default=str))
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["status", "activate", "clear"])
    p.add_argument("name", nargs="?")
    a = p.parse_args()
    logging.basicConfig(level=settings.log_level,
                        format="%(levelname)-7s %(name)s: %(message)s")
    sys.exit(asyncio.run(run(a.action, a.name)))
