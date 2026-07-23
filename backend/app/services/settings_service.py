"""Resolve and update runtime settings — backed by a local JSON config file.

No database table is used. Effective value =
    config file (if key present) -> env default.

The file is read at login time (infrequent) and written by the admin API, so it
persists across restarts and is shared by all worker processes on the host.
"""
import json
from pathlib import Path

from app.core.config import settings

LOGIN_TTL_KEY = "login_ttl_seconds"

# Guardrails for a manually-set login lifetime: 5 minutes .. 365 days.
MIN_LOGIN_TTL = 300
MAX_LOGIN_TTL = 31536000


def _config_path() -> Path:
    return Path(settings.runtime_config_path)


def _read_config() -> dict:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config(data: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_login_ttl() -> int:
    """Effective login-session lifetime in seconds (config file -> env default)."""
    value = _read_config().get(LOGIN_TTL_KEY)
    try:
        return int(value) if value is not None else settings.login_ttl_seconds
    except (TypeError, ValueError):
        return settings.login_ttl_seconds


def set_login_ttl(seconds: int) -> int:
    """Persist a new login-session lifetime to the config file. Returns stored value."""
    if not (MIN_LOGIN_TTL <= seconds <= MAX_LOGIN_TTL):
        raise ValueError(f"login_ttl_seconds 必须在 {MIN_LOGIN_TTL}~{MAX_LOGIN_TTL} 秒之间")
    data = _read_config()
    data[LOGIN_TTL_KEY] = seconds
    _write_config(data)
    return seconds
