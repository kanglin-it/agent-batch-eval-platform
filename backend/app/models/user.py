"""Read-only mapping of the Django ops-backend user table.

We only READ this table for authentication. Password changes / user creation stay
in the Django admin to avoid double-write conflicts. Table name is configurable via
USER_TABLE because a custom AUTH_USER_MODEL changes it (e.g. `users_user`).
"""
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.session import Base


class User(Base):
    __tablename__ = settings.user_table

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    password: Mapped[str] = mapped_column(String(128))  # Django hash string
    email: Mapped[str] = mapped_column(String(254), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
