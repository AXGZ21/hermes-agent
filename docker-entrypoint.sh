#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Hermes Agent – Docker entrypoint
#
# 1. Bootstraps ~/.hermes/ from Railway environment variables
#    (only on first boot; web UI owns the files after that)
# 2. Starts the FastAPI config UI as the primary process
#    (the web UI then launches gateway.run as a child process)
# ──────────────────────────────────────────────────────────────
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/root/.hermes}"

# ── Directory structure ───────────────────────────────────────
mkdir -p "$HERMES_HOME/sessions" "$HERMES_HOME/logs" \
         "$HERMES_HOME/skills"   "$HERMES_HOME/cache"

# ── Bootstrap .env from Railway env vars (only if missing) ────
if [ ! -f "$HERMES_HOME/.env" ]; then
  cat > "$HERMES_HOME/.env" <<EOF
# LLM provider
OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}
LLM_MODEL=${LLM_MODEL:-anthropic/claude-opus-4-6}

# Tools (all optional)
FIRECRAWL_API_KEY=${FIRECRAWL_API_KEY:-}
NOUS_API_KEY=${NOUS_API_KEY:-}
FAL_KEY=${FAL_KEY:-}
HONCHO_API_KEY=${HONCHO_API_KEY:-}
GITHUB_TOKEN=${GITHUB_TOKEN:-}
BROWSERBASE_API_KEY=${BROWSERBASE_API_KEY:-}
BROWSERBASE_PROJECT_ID=${BROWSERBASE_PROJECT_ID:-}
BROWSERBASE_PROXIES=${BROWSERBASE_PROXIES:-true}
VOICE_TOOLS_OPENAI_KEY=${VOICE_TOOLS_OPENAI_KEY:-}

# Terminal backend
TERMINAL_ENV=${TERMINAL_ENV:-local}
TERMINAL_TIMEOUT=${TERMINAL_TIMEOUT:-60}
TERMINAL_LIFETIME_SECONDS=${TERMINAL_LIFETIME_SECONDS:-300}

# Messaging – Telegram
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
TELEGRAM_HOME_CHANNEL=${TELEGRAM_HOME_CHANNEL:-}
TELEGRAM_HOME_CHANNEL_NAME=${TELEGRAM_HOME_CHANNEL_NAME:-}

# Messaging – Discord
DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN:-}
DISCORD_HOME_CHANNEL=${DISCORD_HOME_CHANNEL:-}
DISCORD_HOME_CHANNEL_NAME=${DISCORD_HOME_CHANNEL_NAME:-}

# Messaging – Slack
SLACK_BOT_TOKEN=${SLACK_BOT_TOKEN:-}
SLACK_APP_TOKEN=${SLACK_APP_TOKEN:-}
SLACK_HOME_CHANNEL=${SLACK_HOME_CHANNEL:-}
SLACK_HOME_CHANNEL_NAME=${SLACK_HOME_CHANNEL_NAME:-}

# Messaging – WhatsApp
WHATSAPP_ENABLED=${WHATSAPP_ENABLED:-false}

# Gateway
GATEWAY_ALLOW_ALL_USERS=${GATEWAY_ALLOW_ALL_USERS:-false}
SESSION_IDLE_MINUTES=${SESSION_IDLE_MINUTES:-}

# Context compression
CONTEXT_COMPRESSION_ENABLED=${CONTEXT_COMPRESSION_ENABLED:-true}
CONTEXT_COMPRESSION_THRESHOLD=${CONTEXT_COMPRESSION_THRESHOLD:-0.85}

# Response pacing
HERMES_HUMAN_DELAY_MODE=${HERMES_HUMAN_DELAY_MODE:-off}

# RL / tracking
TINKER_API_KEY=${TINKER_API_KEY:-}
WANDB_API_KEY=${WANDB_API_KEY:-}
RL_API_URL=${RL_API_URL:-http://localhost:8080}
EOF
  echo "[entrypoint] Wrote initial .env to $HERMES_HOME/.env"
fi

# ── Bootstrap config.yaml (minimal skeleton) ─────────────────
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
  cat > "$HERMES_HOME/config.yaml" <<EOF
model:
  default: "${LLM_MODEL:-anthropic/claude-opus-4-6}"
  provider: "auto"
terminal:
  backend: "${TERMINAL_ENV:-local}"
compression:
  enabled: true
  threshold: 0.85
EOF
  echo "[entrypoint] Wrote initial config.yaml"
fi

# ── SOUL.md placeholder ───────────────────────────────────────
if [ ! -f "$HERMES_HOME/SOUL.md" ]; then
  echo "You are Hermes, a helpful AI assistant." > "$HERMES_HOME/SOUL.md"
fi

echo "[entrypoint] Hermes home: $HERMES_HOME"
echo "[entrypoint] Starting config UI on port ${PORT:-8080}"

# ── Start the FastAPI config UI (primary process) ─────────────
# The web UI manages gateway.run as a child process internally.
exec python -m uvicorn web_ui.app:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}" \
  --log-level info
