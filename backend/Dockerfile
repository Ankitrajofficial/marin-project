# ORCA backend image.
#
# Build context is the REPO ROOT, not backend/, because the image needs db/*.sql
# as well: the container applies its own schema at boot (see jobs/init_db.py),
# since a managed Postgres offers no docker-entrypoint-initdb.d to mount into.
#
#   docker build -f backend/Dockerfile -t orca-api .

FROM python:3.11-slim

# Python 3.11 exactly, matching backend/.python-version. The scientific wheels
# in requirements.txt are what pin this: they are not reliably prebuilt for
# 3.14, and a source build of scipy on a free-tier builder times out.

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

# Requirements first, as their own layer: application code changes on every
# push, dependencies almost never, and reinstalling scipy on each deploy is
# minutes of free-tier build time for nothing.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Layout mirrors the repo so jobs/init_db.py finds db/ where it expects:
# it resolves parents[2] from backend/jobs/, which is /app here.
COPY backend/app ./app
COPY backend/jobs ./jobs
COPY db /app/db

# Schema before server, every boot. Safe to repeat: every statement in db/ is
# idempotent (IF NOT EXISTS, if_not_exists => TRUE, ON CONFLICT DO NOTHING),
# so this converges an empty database and no-ops on a populated one.
#
# `exec` so uvicorn becomes PID 1 and receives SIGTERM directly -- without it
# the shell swallows the signal and the platform kills the container on a
# timeout instead of letting connections drain.
#
# $PORT is injected by the host (Render, Cloud Run, Fly all set it); 8000 is
# the local default. 0.0.0.0, not 127.0.0.1: the loopback bind that is correct
# beside a local Next server makes the container unreachable from outside it.
CMD ["sh", "-c", "python -m jobs.init_db && exec uvicorn app.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
