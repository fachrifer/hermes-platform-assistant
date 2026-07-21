# Hermes Agent — single lightweight image.
# Python core runtime.
FROM python:3.11-slim

# Install curl for healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin hermes

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY config/ ./config/
COPY core/ ./core/
COPY connectors/ ./connectors/
COPY scripts/ ./scripts/

# Data & credentials are mounted at runtime (cloud-synced folder).
RUN mkdir -p /app/data /app/credentials /app/data/reference \
    && chown -R hermes:hermes /app

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app
USER hermes

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD sh -c 'if [ -n "$HERMES_HEALTH_TOKEN" ]; then curl -fsS -H "X-Hermes-Health-Token: $HERMES_HEALTH_TOKEN" http://localhost:8000/health; else curl -fsS http://localhost:8000/health; fi || exit 1'

CMD ["python", "-m", "uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8000"]
