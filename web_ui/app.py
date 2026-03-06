"""
Hermes Agent – Web Configuration UI
FastAPI backend that:
  • Reads/writes ~/.hermes/config.yaml and .env
  • Proxies the Nous Portal OAuth device-code flow
  • Manages the gateway as a child process
"""

import asyncio
import json
import os
import subprocess
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Deque, Optional

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
AUTH_PATH = HERMES_HOME / "auth.json"

NOUS_PORTAL = "https://portal.nousresearch.com"
CLIENT_ID = "hermes-cli"

# ── Gateway process state ─────────────────────────────────────
_gw_proc: Optional[subprocess.Popen] = None
_gw_logs: Deque[str] = deque(maxlen=200)
_gw_started_at: Optional[float] = None
_oauth_state: dict = {}


# ── File helpers ──────────────────────────────────────────────
def _home() -> Path:
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    return HERMES_HOME


def read_config() -> dict:
    if CONFIG_PATH.exists():
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return {}


def save_config(cfg: dict) -> None:
    _home()
    CONFIG_PATH.write_text(
        yaml.dump(cfg, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )


def read_env() -> dict:
    if not ENV_PATH.exists():
        return {}
    out: dict = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def save_env(env: dict) -> None:
    _home()
    ENV_PATH.write_text(
        "\n".join(f"{k}={v}" for k, v in env.items() if v is not None) + "\n"
    )


def patch_env(**kv) -> None:
    e = read_env()
    for k, v in kv.items():
        if v is not None:
            e[k] = v
    save_env(e)


def read_auth() -> dict:
    if AUTH_PATH.exists():
        try:
            return json.loads(AUTH_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_auth(a: dict) -> None:
    _home()
    AUTH_PATH.write_text(json.dumps(a, indent=2))


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "••••••••"
    return "••••" + v[-4:]


# ── Gateway management ────────────────────────────────────────
def _gw_running() -> bool:
    return _gw_proc is not None and _gw_proc.poll() is None


def _drain_logs(proc: subprocess.Popen) -> None:
    """Non-blocking read of any available gateway output."""
    try:
        import select
        while proc.stdout and select.select([proc.stdout], [], [], 0)[0]:
            line = proc.stdout.readline()
            if line:
                _gw_logs.append(line.rstrip())
    except Exception:
        pass


def start_gateway() -> None:
    global _gw_proc, _gw_started_at
    if _gw_running():
        return
    _gw_logs.clear()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # Load ~/.hermes/.env into subprocess env
    for k, v in read_env().items():
        env.setdefault(k, v)
    _gw_proc = subprocess.Popen(
        ["python", "-m", "gateway.run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd="/app",
    )
    _gw_started_at = time.time()


def stop_gateway() -> None:
    global _gw_proc, _gw_started_at
    if _gw_proc and _gw_proc.poll() is None:
        _gw_proc.terminate()
        try:
            _gw_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _gw_proc.kill()
    _gw_proc = None
    _gw_started_at = None


async def _log_pump() -> None:
    """Background task: drain gateway logs periodically."""
    while True:
        if _gw_proc and _gw_proc.poll() is None:
            _drain_logs(_gw_proc)
        await asyncio.sleep(1)


# ── Lifespan ──────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start gateway on boot (best-effort – may have no platforms yet)
    try:
        start_gateway()
    except Exception:
        pass
    asyncio.create_task(_log_pump())
    yield
    stop_gateway()


app = FastAPI(title="Hermes Config UI", lifespan=lifespan)

# ── Static files ──────────────────────────────────────────────
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
async def root():
    return FileResponse(_static_dir / "index.html")


# ── Overview / Status ─────────────────────────────────────────
@app.get("/api/status")
async def api_status():
    env = read_env()
    auth = read_auth()
    cfg = read_config()

    nous = auth.get("providers", {}).get("nous", {})
    model_cfg = cfg.get("model", {})
    base_url = model_cfg.get("base_url", "")
    provider_type = model_cfg.get("provider", "auto")

    ptype = "openrouter"
    if provider_type == "nous" or "nousresearch" in base_url:
        ptype = "nous"
    elif base_url and base_url not in ("https://openrouter.ai/api/v1", ""):
        ptype = "custom"

    return {
        "nous_logged_in": bool(nous.get("refresh_token") or nous.get("access_token")),
        "openrouter_key": bool(env.get("OPENROUTER_API_KEY")),
        "provider_type": ptype,
        "active_model": model_cfg.get("default", ""),
        "custom_base_url": base_url,
        "gateway": {
            "running": _gw_running(),
            "pid": _gw_proc.pid if _gw_running() else None,
            "uptime_s": int(time.time() - _gw_started_at) if _gw_started_at else 0,
        },
        "platforms": {
            "telegram": bool(env.get("TELEGRAM_BOT_TOKEN")),
            "discord": bool(env.get("DISCORD_BOT_TOKEN")),
            "slack": bool(env.get("SLACK_BOT_TOKEN")),
            "whatsapp": env.get("WHATSAPP_ENABLED", "").lower() in ("true", "1", "yes"),
        },
    }


@app.get("/api/gateway/logs")
async def get_gateway_logs():
    if _gw_proc:
        _drain_logs(_gw_proc)
    return {"logs": list(_gw_logs)}


@app.post("/api/gateway/restart")
async def restart_gateway():
    stop_gateway()
    await asyncio.sleep(0.5)
    start_gateway()
    return {"ok": True, "pid": _gw_proc.pid if _gw_proc else None}


# ── Nous Portal OAuth ─────────────────────────────────────────
@app.post("/api/auth/nous/start")
async def nous_start():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{NOUS_PORTAL}/api/oauth/device/code",
            json={"client_id": CLIENT_ID, "scope": "inference:mint_agent_key"},
        )
        if r.status_code != 200:
            raise HTTPException(502, f"Nous Portal error {r.status_code}: {r.text[:200]}")
        d = r.json()
    _oauth_state["device_code"] = d["device_code"]
    _oauth_state["interval"] = d.get("interval", 5)
    return {
        "user_code": d["user_code"],
        "verification_uri": d["verification_uri"],
        "expires_in": d.get("expires_in", 300),
    }


@app.get("/api/auth/nous/poll")
async def nous_poll():
    if not _oauth_state.get("device_code"):
        raise HTTPException(400, "No active OAuth flow")
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{NOUS_PORTAL}/api/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": _oauth_state["device_code"],
            },
        )
        d = r.json()

    if "access_token" in d:
        auth = read_auth()
        auth.setdefault("providers", {})["nous"] = {
            "access_token": d["access_token"],
            "refresh_token": d.get("refresh_token"),
            "expires_in": d.get("expires_in"),
        }
        auth["active_provider"] = "nous"
        save_auth(auth)
        # Update config to use nous provider
        cfg = read_config()
        cfg.setdefault("model", {})["provider"] = "nous"
        cfg["model"]["base_url"] = "https://inference-api.nousresearch.com/v1"
        save_config(cfg)
        _oauth_state.clear()
        return {"status": "authorized"}

    err = d.get("error", "unknown")
    if err in ("authorization_pending", "slow_down"):
        return {"status": "pending"}
    _oauth_state.clear()
    return {"status": "error", "detail": err}


@app.post("/api/auth/nous/logout")
async def nous_logout():
    auth = read_auth()
    auth.get("providers", {}).pop("nous", None)
    if auth.get("active_provider") == "nous":
        auth.pop("active_provider", None)
    save_auth(auth)
    cfg = read_config()
    if cfg.get("model", {}).get("provider") == "nous":
        cfg["model"]["provider"] = "auto"
    save_config(cfg)
    return {"ok": True}


@app.get("/api/auth/status")
async def auth_status():
    auth = read_auth()
    nous = auth.get("providers", {}).get("nous", {})
    return {
        "nous": {"logged_in": bool(nous.get("refresh_token") or nous.get("access_token"))},
        "active_provider": auth.get("active_provider", "auto"),
    }


# ── Provider config ───────────────────────────────────────────
class ProviderIn(BaseModel):
    type: str  # "nous" | "openrouter" | "custom"
    api_key: str = ""
    base_url: str = ""
    model: str = ""


@app.get("/api/provider")
async def get_provider():
    cfg = read_config()
    env = read_env()
    model_cfg = cfg.get("model", {})
    base_url = model_cfg.get("base_url", "")
    provider = model_cfg.get("provider", "auto")

    ptype = "openrouter"
    if provider == "nous" or "nousresearch" in base_url:
        ptype = "nous"
    elif base_url and base_url not in ("https://openrouter.ai/api/v1", ""):
        ptype = "custom"

    key = env.get("OPENROUTER_API_KEY", "")
    return {
        "type": ptype,
        "api_key_set": bool(key),
        "api_key_masked": _mask(key),
        "base_url": base_url,
        "model": model_cfg.get("default", "anthropic/claude-opus-4-6"),
    }


@app.post("/api/provider")
async def set_provider(p: ProviderIn):
    cfg = read_config()
    cfg.setdefault("model", {})

    if p.type == "nous":
        cfg["model"]["provider"] = "nous"
        cfg["model"]["base_url"] = "https://inference-api.nousresearch.com/v1"
    elif p.type == "openrouter":
        cfg["model"]["provider"] = "openrouter"
        cfg["model"]["base_url"] = "https://openrouter.ai/api/v1"
        if p.api_key:
            patch_env(OPENROUTER_API_KEY=p.api_key)
    elif p.type == "custom":
        cfg["model"]["provider"] = "openrouter"  # LiteLLM-compatible passthrough
        if p.base_url:
            cfg["model"]["base_url"] = p.base_url
        if p.api_key:
            patch_env(OPENROUTER_API_KEY=p.api_key)

    if p.model:
        cfg["model"]["default"] = p.model

    save_config(cfg)
    return {"ok": True}


# ── Messaging platforms ───────────────────────────────────────
class PlatformIn(BaseModel):
    telegram_token: str = ""
    telegram_channel: str = ""
    discord_token: str = ""
    discord_channel: str = ""
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_channel: str = ""
    whatsapp_enabled: bool = False
    allow_all_users: bool = False


@app.get("/api/platforms")
async def get_platforms():
    env = read_env()

    def m(k: str) -> str:
        v = env.get(k, "")
        return _mask(v) if v else ""

    return {
        "telegram_token_set": bool(env.get("TELEGRAM_BOT_TOKEN")),
        "telegram_token_masked": m("TELEGRAM_BOT_TOKEN"),
        "telegram_channel": env.get("TELEGRAM_HOME_CHANNEL", ""),
        "discord_token_set": bool(env.get("DISCORD_BOT_TOKEN")),
        "discord_token_masked": m("DISCORD_BOT_TOKEN"),
        "discord_channel": env.get("DISCORD_HOME_CHANNEL", ""),
        "slack_bot_token_set": bool(env.get("SLACK_BOT_TOKEN")),
        "slack_bot_token_masked": m("SLACK_BOT_TOKEN"),
        "slack_app_token_set": bool(env.get("SLACK_APP_TOKEN")),
        "slack_app_token_masked": m("SLACK_APP_TOKEN"),
        "slack_channel": env.get("SLACK_HOME_CHANNEL", ""),
        "whatsapp_enabled": env.get("WHATSAPP_ENABLED", "").lower() in ("true", "1", "yes"),
        "allow_all_users": env.get("GATEWAY_ALLOW_ALL_USERS", "").lower() in ("true", "1"),
    }


@app.post("/api/platforms")
async def set_platforms(p: PlatformIn):
    updates: dict = {}
    if p.telegram_token:
        updates["TELEGRAM_BOT_TOKEN"] = p.telegram_token
    if p.telegram_channel:
        updates["TELEGRAM_HOME_CHANNEL"] = p.telegram_channel
    if p.discord_token:
        updates["DISCORD_BOT_TOKEN"] = p.discord_token
    if p.discord_channel:
        updates["DISCORD_HOME_CHANNEL"] = p.discord_channel
    if p.slack_bot_token:
        updates["SLACK_BOT_TOKEN"] = p.slack_bot_token
    if p.slack_app_token:
        updates["SLACK_APP_TOKEN"] = p.slack_app_token
    if p.slack_channel:
        updates["SLACK_HOME_CHANNEL"] = p.slack_channel
    updates["WHATSAPP_ENABLED"] = "true" if p.whatsapp_enabled else "false"
    updates["GATEWAY_ALLOW_ALL_USERS"] = "true" if p.allow_all_users else "false"
    patch_env(**updates)
    return {"ok": True}


# ── Settings ──────────────────────────────────────────────────
class SettingsIn(BaseModel):
    terminal_backend: str = "local"
    max_turns: int = 60
    compression_enabled: bool = True
    compression_threshold: float = 0.85
    human_delay_mode: str = "off"
    firecrawl_key: str = ""
    fal_key: str = ""
    github_token: str = ""
    browserbase_key: str = ""
    browserbase_project: str = ""


@app.get("/api/settings")
async def get_settings():
    cfg = read_config()
    env = read_env()
    return {
        "terminal_backend": cfg.get("terminal", {}).get("backend", "local"),
        "max_turns": cfg.get("agent", {}).get("max_turns", 60),
        "compression_enabled": cfg.get("compression", {}).get("enabled", True),
        "compression_threshold": cfg.get("compression", {}).get("threshold", 0.85),
        "human_delay_mode": cfg.get("human_delay", {}).get("mode", "off"),
        "firecrawl_key_set": bool(env.get("FIRECRAWL_API_KEY")),
        "fal_key_set": bool(env.get("FAL_KEY")),
        "github_token_set": bool(env.get("GITHUB_TOKEN")),
        "browserbase_key_set": bool(env.get("BROWSERBASE_API_KEY")),
        "browserbase_project_set": bool(env.get("BROWSERBASE_PROJECT_ID")),
    }


@app.post("/api/settings")
async def save_settings(s: SettingsIn):
    cfg = read_config()
    cfg.setdefault("terminal", {})["backend"] = s.terminal_backend
    cfg.setdefault("agent", {})["max_turns"] = s.max_turns
    cfg.setdefault("compression", {})["enabled"] = s.compression_enabled
    cfg.setdefault("compression", {})["threshold"] = s.compression_threshold
    cfg.setdefault("human_delay", {})["mode"] = s.human_delay_mode
    save_config(cfg)

    env_updates: dict = {}
    if s.firecrawl_key:
        env_updates["FIRECRAWL_API_KEY"] = s.firecrawl_key
    if s.fal_key:
        env_updates["FAL_KEY"] = s.fal_key
    if s.github_token:
        env_updates["GITHUB_TOKEN"] = s.github_token
    if s.browserbase_key:
        env_updates["BROWSERBASE_API_KEY"] = s.browserbase_key
    if s.browserbase_project:
        env_updates["BROWSERBASE_PROJECT_ID"] = s.browserbase_project
    if env_updates:
        patch_env(**env_updates)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), log_level="info")
