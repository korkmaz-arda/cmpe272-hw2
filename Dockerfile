# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Dependencies first so they stay cached across source-only changes.
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir .

# The image supplies the required PORT variable (the application itself has no
# default) and stores the webhook database on a volume so deliveries survive a
# container restart. Both are overridable with --env-file / -e.
ENV PORT=8000 \
    DB_PATH=/data/webhooks.db

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /srv
USER appuser

VOLUME ["/data"]
EXPOSE 8000

# `sh -c` so ${PORT} is expanded at runtime; `exec` so uvicorn becomes PID 1 and
# receives SIGTERM directly on `docker stop`.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
