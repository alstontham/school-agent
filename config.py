"""Loads settings from .env (on the Mac) or environment variables (on GitHub Actions)."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def require(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


CANVAS_BASE_URL = os.environ.get("CANVAS_BASE_URL", "https://canvas.mit.edu").rstrip("/")
CANVAS_TOKEN = require("CANVAS_TOKEN")
TELEGRAM_BOT_TOKEN = require("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = require("TELEGRAM_CHAT_ID")
TZ = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
