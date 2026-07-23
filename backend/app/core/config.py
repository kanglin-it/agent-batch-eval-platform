from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (shared with the Django ops backend)
    database_url: str = "mysql+aiomysql://user:password@127.0.0.1:3306/ops_backend"
    user_table: str = "auth_user"

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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
