from pydantic import BaseModel


class LoginRequest(BaseModel):
    phone: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int | None = None  # login-session lifetime in seconds


class CurrentUser(BaseModel):
    id: int
    username: str
    phone: str | None = None
    is_staff: bool = False
    is_superuser: bool = False
