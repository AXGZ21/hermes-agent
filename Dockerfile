# ── Hermes Agent — Railway Deployment Image ─────────────────────────────────
# Targets Python 3.11+ (required by hermes-agent).
# Installs the core package plus the messaging extras so Telegram, Discord,
# Slack, and other platform gateways are available out of the box.

FROM python:3.11-slim

# System dependencies
# - git        : skills hub / submodule support
# - ffmpeg     : voice / audio tool support
# - libsndfile1: audio processing dependency
# - curl       : health probes and download helpers
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        ffmpeg \
        libsndfile1 \
        tmux \
    && rm -rf /var/lib/apt/lists/*

# Install ttyd for browser-based web terminal
RUN curl -fsSL https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
        -o /usr/local/bin/ttyd \
    && chmod +x /usr/local/bin/ttyd

WORKDIR /app

# Copy dependency manifests first so Docker layer cache is reused
# when only source files change.
COPY pyproject.toml requirements.txt ./

# Install dependencies only (no -e yet, so cache is reused when source changes)
RUN pip install --no-cache-dir -r requirements.txt 2>/dev/null || true

# Copy the full source
COPY . .

# Editable install now that all source is present — ensures hermes_cli and all
# modules are discoverable from /app
RUN pip install --no-cache-dir -e ".[messaging,cron,cli]"

# Initialize git submodules (mini-swe-agent etc.).
# Falls back gracefully on shallow clones where submodules may be unavailable.
RUN git submodule update --init --recursive 2>/dev/null || \
    echo "[railway] Note: submodule init skipped (shallow clone)"

# Persistent workspace directory mounted by the terminal tool
RUN mkdir -p /workspace

# Entrypoint script writes ~/.hermes/.env and config.yaml from Railway env
# vars, then starts the gateway.
RUN chmod +x /app/docker-entrypoint.sh

# hermes reads HERMES_HOME to locate ~/.hermes equivalents inside the container
ENV HERMES_HOME=/root/.hermes
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
