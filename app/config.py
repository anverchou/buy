"""Configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_config() -> dict:
    instance = BASE_DIR / "instance"
    instance.mkdir(exist_ok=True)
    return {
        "SECRET_KEY": os.environ.get("SECRET_KEY") or "dev-only-change-me",
        "DATABASE_URL": os.environ.get("DATABASE_URL") or f"sqlite:///{instance / 'prices.db'}",
        # auto = live adapters when any API key is set, otherwise mock data
        "DATA_MODE": (os.environ.get("DATA_MODE") or "auto").strip().lower(),
        "SERPAPI_API_KEY": os.environ.get("SERPAPI_API_KEY", "").strip(),
        "SERPAPI_MONTHLY_LIMIT": _int("SERPAPI_MONTHLY_LIMIT", 250),
        "KEEPA_API_KEY": os.environ.get("KEEPA_API_KEY", "").strip(),
        "KEEPA_DOMAIN": _int("KEEPA_DOMAIN", 1),  # 1 = amazon.com
        "APP_TIMEZONE": os.environ.get("APP_TIMEZONE") or "America/Los_Angeles",
        "APP_PASSWORD": os.environ.get("APP_PASSWORD", ""),
        "APP_BASE_URL": os.environ.get("APP_BASE_URL", "").rstrip("/"),
        "NTFY_TOPIC": os.environ.get("NTFY_TOPIC", "").strip(),
        "NTFY_SERVER": (os.environ.get("NTFY_SERVER") or "https://ntfy.sh").rstrip("/"),
        "STALE_AFTER_DAYS": _int("STALE_AFTER_DAYS", 7),
        "MOCK_SEED": _int("MOCK_SEED", 42),
        "AUTO_SEED_DEMO": _bool("AUTO_SEED_DEMO", True),
        "HTTP_RETRY_DELAY": float(os.environ.get("HTTP_RETRY_DELAY", "2") or 2),
    }


def effective_mode(config: dict) -> str:
    mode = config.get("DATA_MODE", "auto")
    if mode in {"mock", "live"}:
        return mode
    return "live" if (config.get("SERPAPI_API_KEY") or config.get("KEEPA_API_KEY")) else "mock"
