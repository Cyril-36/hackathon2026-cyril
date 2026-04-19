# ShopWave Autonomous Support Resolution Agent — reproducible container image.
#
# Matches the brief's "Docker = bonus points" ask (Slide 7). The default
# CMD runs the same deterministic rules-mode pass the verifier uses, so
#   docker build -t shopwave-agent . && docker run --rm shopwave-agent
# produces the canonical 20-ticket summary with no host dependencies.
#
# Notes on the image:
#   - python:3.11-slim keeps the layer size small; the agent has no C-ext deps.
#   - We copy requirements.txt first and pip-install in its own layer so that
#     rebuilds after code-only edits stay in the warm cache.
#   - Non-root `agent` user — pytest + file I/O work fine under unprivileged
#     uid, and prod-like runs shouldn't need root.
#   - No network is required for `--mode rules`. The image intentionally has
#     no .env baked in; if you want to exercise hybrid/llm mode, pass the key
#     at runtime: `docker run --rm -e GROQ_API_KEY=... shopwave-agent`.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Then the source + data + tests (everything the verifier needs).
COPY app ./app
COPY data ./data
COPY tests ./tests
COPY run.py ./
COPY scripts ./scripts
COPY frontend ./frontend
COPY audit_log.json ./audit_log.json
COPY audit_log_chaos_seed42.json ./audit_log_chaos_seed42.json

# Run as a non-root user.
RUN chmod +x /app/scripts/docker-entrypoint.sh \
    && useradd --create-home --uid 1000 agent \
    && chown -R agent:agent /app
USER agent

# Optional web-UI port (only bound when launched as `... web`).
EXPOSE 8787

# Dispatcher routes:
#   (no arg)   → python run.py --mode rules --chaos 0   (same as verifier)
#   web        → uvicorn app.server:app on :8787
#   test       → pytest
#   anything   → pass-through
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD []
