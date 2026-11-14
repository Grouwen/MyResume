from datetime import datetime

from sqlalchemy import BigInteger, Uuid, DateTime, func, Index, String, Integer
from sqlalchemy.orm import Mapped, mapped_column
import uuid
from app.models.base import Base


class Session(Base):
    __tablename__ = "session"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )
    session_pk: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        unique=True,
        default=uuid.uuid4
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False
    )

    thread_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        unique=True
    )

    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    message_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    last_active: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    __table_args__ = (
        Index("ix_session_user_last_active", "user_id", "last_active"),
        Index("ix_session_last_active", "last_active")
    )