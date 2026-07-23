"""Read-only mapping of the ops-platform user table (default t_operation_user).

That model extends Django's AbstractUser, so `password` holds a standard Django
hash (pbkdf2_sha256 …) which passlib verifies directly. Login is by `username`.
We only READ this table; user/password management stays in the ops platform. The
table name is configurable via [database] user_table.
"""
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.session import Base


class User(Base):
    __tablename__ = settings.user_table

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(30), unique=True)  # login identifier
    username: Mapped[str] = mapped_column(String(150), unique=True)
    password: Mapped[str] = mapped_column(String(128))  # Django hash string
    email: Mapped[str] = mapped_column(String(254), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_staff: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login: Mapped[DateTime | None] = mapped_column(DateTime, nullable=True)
