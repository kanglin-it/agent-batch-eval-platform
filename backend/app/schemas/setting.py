from pydantic import BaseModel, Field


class LoginTtlResponse(BaseModel):
    login_ttl_seconds: int
    login_ttl_days: float


class UpdateLoginTtlRequest(BaseModel):
    # Provide seconds directly, or days for convenience (days wins if both given).
    login_ttl_seconds: int | None = Field(default=None, ge=1)
    login_ttl_days: float | None = Field(default=None, gt=0)
