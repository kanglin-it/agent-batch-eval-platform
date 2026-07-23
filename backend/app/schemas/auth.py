from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int | None = None  # login-session lifetime in seconds


class CurrentUser(BaseModel):
    id: int
    username: str
    is_staff: bool = False
    is_superuser: bool = False
