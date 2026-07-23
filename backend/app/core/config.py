from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (shared with the Django ops backend) — used for login (auth_user).
    database_url: str = "mysql+aiomysql://user:password@127.0.0.1:3306/ops_backend"
    user_table: str = "auth_user"

    # Case-data database (PostgreSQL 'saas') — holds the history dataset tables that
    # 用例管理页 queries. Read directly; separate from the login database.
    case_database_url: str = "postgresql+asyncpg://user:password@127.0.0.1:5432/saas"
    case_qa_table: str = "t_history_qa_dataset"
    case_review_table: str = "t_history_review_dataset"

    # JWT — this platform's own token, unrelated to Django SECRET_KEY
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    # Default login-session lifetime. Env value is only the DEFAULT; the effective
    # value is read at login time and can be overridden at runtime via the JSON
    # config file (see services/settings_service.py). 30 days = 2592000s.
    login_ttl_seconds: int = 2592000

    # Path to the runtime-editable JSON config file (holds admin overrides such as
    # login_ttl_seconds). No database table is used.
    runtime_config_path: str = "runtime_config.json"

    # CORS
    cors_origins: str = "http://localhost:5173"

    # External evaluation services
    coze_api_base: str = "https://api.coze.cn"
    coze_api_token: str = ""
    agent_api_base: str = ""

    # ---- Zhiexa Agent (sandbox conversation) — the Agent that reruns cases ----
    # NOTE: these are credentials; overridable via env. Prefer injecting the
    # password from a secret store rather than relying on the default.
    zhiexa_login_url: str = "https://www.zhiexa.com/zhiexa/saas/api/v1/auth/password/login"
    zhiexa_skill_base: str = "https://skill.zhiexa.com"
    zhiexa_phone: str = "15067062596"
    zhiexa_password: str = "jBlBo5Iw7CcOpm7L5VS/9Q=="
    zhiexa_channel_type: str = "PC"
    zhiexa_jwt_ttl_seconds: int = 518400        # cache exchanged JWT ~6 days (JWT valid 7)
    zhiexa_chat_timeout: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
