"""Runtime-editable key/value settings owned by this platform.

Used for values an admin should be able to change WITHOUT a redeploy/restart —
currently the login-session lifetime (`login_ttl_seconds`).
"""
import datetime as dt

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PlatformSetting(Base):
    __tablename__ = "platform_setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(500))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime,
        default=lambda: dt.datetime.now(dt.timezone.utc),
        onupdate=lambda: dt.datetime.now(dt.timezone.utc),
    )


# Known setting keys
LOGIN_TTL_SECONDS = "login_ttl_seconds"
