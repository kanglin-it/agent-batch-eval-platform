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
        # strict=False tolerates duplicate sections/keys (last value wins).
        # inline_comment_prefixes=(";",) strips trailing " ; 注释" from values (e.g. the
        # "; ← 必填" hints in the config template). NOT "#" — DB passwords contain '#'.
        cp = configparser.ConfigParser(
            interpolation=None, strict=False, inline_comment_prefixes=(";",)
        )
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

        # ---- WRITE database (read-write primary): platform tables (eval_task*) ----
        db_host = s("database", "host", "127.0.0.1")
        db_port = i("database", "port", 5432)
        db_user = s("database", "user")
        db_pass = s("database", "password")
        db_name = s("database", "dbname", "saas")
        self.user_table = s("database", "user_table", "t_operation_user")
        self.database_url = _pg_url(db_host, db_port, db_user, db_pass, db_name)

        # ---- READ-ONLY database (replica): case data + login user table (t_operation_user).
        # Point this at the read-only replica; blank fields fall back to [database].
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

        # ---- login (ops-platform password API; fast, replaces local hash verify) ----
        self.operation_login_url = s(
            "auth", "login_url",
            "https://test-opr.zhiexa.com/zhiexa/operation/api/v1/user/login/password/",
        )
        self.operation_login_timeout = i("auth", "login_timeout", 15)
        # Only these phones get superuser (可改「系统设置」). Comma-separated.
        self.admin_phones = [
            p.strip() for p in s("auth", "admin_phones", "15067062596").split(",") if p.strip()
        ]

        # ---- Zhiexa Agent ----
        self.zhiexa_login_url = s("zhiexa", "login_url",
                                  "https://www.zhiexa.com/zhiexa/saas/api/v1/auth/password/login")
        self.zhiexa_skill_base = s("zhiexa", "skill_base", "https://staging-skill.zhiexa.com")
        self.zhiexa_phone = s("zhiexa", "phone")
        self.zhiexa_password = s("zhiexa", "password")
        self.zhiexa_channel_type = s("zhiexa", "channel_type", "PC")
        self.zhiexa_jwt_ttl_seconds = i("zhiexa", "jwt_ttl_seconds", 518400)
        self.zhiexa_chat_timeout = i("zhiexa", "chat_timeout", 300)

        # ---- Coze evaluation workflow (Stage 2 对比评测) ----
        self.coze_api_base = s("coze", "api_base", "https://api.coze.cn")
        self.coze_api_token = s("coze", "api_token")
        self.coze_timeout = i("coze", "timeout", 120)

        # ---- File-library service (resolve QA doc_ids/project_id -> oss_url) ----
        self.library_service = s("library", "service")
        self.library_token = s("library", "token")  # optional Bearer auth
        self.library_timeout = i("library", "timeout", 20)

        # ---- SaaS OSS file AES (encrypted .txt objects) ----
        # Primary + optional file_key_2/iv_2, file_key_3/iv_3 … from [crypt].
        self.aes_key_candidates: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def _add_aes(key: str, iv: str) -> None:
            key, iv = (key or "").strip(), (iv or "").strip()
            if not key or not iv:
                return
            pair = (key, iv)
            if pair not in seen:
                seen.add(pair)
                self.aes_key_candidates.append(pair)

        _add_aes(s("crypt", "file_key"), s("crypt", "iv"))
        for n in range(2, 6):
            _add_aes(s("crypt", f"file_key_{n}"), s("crypt", f"iv_{n}"))
        # Last-resort built-in fallbacks if [crypt] is missing entirely.
        if not self.aes_key_candidates:
            _add_aes(
                "55c897adcf75ed57909eb3ca909b7659",
                "1969735b2fcac313063827dc5fcd0cb4",
            )
            _add_aes(
                "9352cd03eb310dea14b2d43de7e1c188",
                "8591e40e6f381a9c3ed8c153fa15369b",
            )

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
