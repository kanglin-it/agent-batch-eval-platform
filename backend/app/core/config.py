"""Configuration loaded from a plain `config.ini` (easy to read/edit).

DB connection is given as discrete fields (host/port/user/password/dbname) and the
SQLAlchemy URL is built in code — so passwords with special characters (@ # $ ! ) …)
need NO URL-encoding. Interpolation is disabled so literal `%` is fine too.

Lookup order for the file: $CONFIG_FILE, ./config.ini, then backend/config.ini.
Missing keys fall back to sensible defaults so the app still boots.
"""
import configparser
import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import URL

PG_DRIVER = "postgresql+asyncpg"


def _find_config() -> Path:
    env = os.getenv("CONFIG_FILE")
    if env:
        return Path(env)
    here = Path(__file__).resolve()          # app/core/config.py
    backend_dir = here.parents[2]            # backend/
    for c in (Path.cwd() / "config.ini", backend_dir / "config.ini"):
        if c.exists():
            return c
    return backend_dir / "config.ini"


class Settings:
    def __init__(self) -> None:
        cp = configparser.ConfigParser(interpolation=None)
        path = _find_config()
        self.config_path = str(path)
        if path.exists():
            cp.read(path, encoding="utf-8")

        def s(sec: str, key: str, default: str = "") -> str:
            return cp.get(sec, key, fallback=default) if cp.has_section(sec) else default

        def i(sec: str, key: str, default: int) -> int:
            try:
                return cp.getint(sec, key)
            except (configparser.Error, ValueError):
                return default

        # ---- login database (PostgreSQL; holds the user table for /api/auth/login) ----
        db_host = s("database", "host", "127.0.0.1")
        db_port = i("database", "port", 5432)
        db_user = s("database", "user")
        db_pass = s("database", "password")
        db_name = s("database", "dbname", "saas")
        self.user_table = s("database", "user_table", "t_operation_user")
        self.database_url = _pg_url(db_host, db_port, db_user, db_pass, db_name)

        # ---- case database (defaults to the same server as [database]) ----
        c_host = s("case_database", "host") or db_host
        c_port = i("case_database", "port", 0) or db_port
        c_user = s("case_database", "user") or db_user
        c_pass = s("case_database", "password") or db_pass
        c_name = s("case_database", "dbname") or db_name
        self.case_qa_table = s("case_database", "qa_table", "t_history_qa_dataset")
        self.case_review_table = s("case_database", "review_table", "t_history_review_dataset")
        self.case_schema = s("case_database", "schema", "public")
        self.case_database_url = _pg_url(c_host, c_port, c_user, c_pass, c_name)

        # ---- JWT (this platform's own token) ----
        self.jwt_secret = s("jwt", "secret", "change-me")
        self.jwt_algorithm = s("jwt", "algorithm", "HS256")
        self.login_ttl_seconds = i("jwt", "login_ttl_seconds", 2592000)

        # ---- app ----
        self.runtime_config_path = s("app", "runtime_config_path", "runtime_config.json")
        self.cors_origins = s("app", "cors_origins", "http://localhost:5173")

        # ---- Zhiexa Agent ----
        self.zhiexa_login_url = s("zhiexa", "login_url",
                                  "https://www.zhiexa.com/zhiexa/saas/api/v1/auth/password/login")
        self.zhiexa_skill_base = s("zhiexa", "skill_base", "https://skill.zhiexa.com")
        self.zhiexa_phone = s("zhiexa", "phone")
        self.zhiexa_password = s("zhiexa", "password")
        self.zhiexa_channel_type = s("zhiexa", "channel_type", "PC")
        self.zhiexa_jwt_ttl_seconds = i("zhiexa", "jwt_ttl_seconds", 518400)
        self.zhiexa_chat_timeout = i("zhiexa", "chat_timeout", 300)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def _pg_url(host: str, port: int, user: str, password: str, dbname: str) -> URL:
    """Build a SQLAlchemy async PG URL; URL.create escapes special chars for us."""
    return URL.create(
        PG_DRIVER,
        username=user or None,
        password=password or None,
        host=host or None,
        port=port or None,
        database=dbname or None,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
