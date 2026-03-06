# ──────────────────────────────────────────────────────────
# Hermes Agent – Railway Deployment Image
# Runs:
#   • FastAPI config UI on $PORT (default 8080)  ← primary process
#   • gateway.run as a managed child process      ← started by FastAPI
# ──────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System deps ────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ripgrep ffmpeg build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js 22 (browser tools) ─────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── App source ─────────────────────────────────────────────
WORKDIR /app
COPY . .

# ── Git submodules (mini-swe-agent, tinker-atropos) ────────
RUN git submodule update --init --recursive 2>/dev/null || true

# ── Python: hermes + web UI deps ───────────────────────────
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[all]" 2>/dev/null \
    || pip install --no-cache-dir -e . \
    && pip install --no-cache-dir "fastapi>=0.111" "uvicorn[standard]>=0.29" "httpx>=0.27" pyyaml

# ── Node deps (browser automation) ─────────────────────────
RUN if [ -f package.json ]; then npm install --omit=dev; fi

# ── Entrypoint ─────────────────────────────────────────────
RUN chmod +x /app/docker-entrypoint.sh
ENTRYPOINT ["/app/docker-entrypoint.sh"]
