import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Text, String, DateTime, func, Index, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enum.running_chat_type_enum import RunningChatTypeEnum
from app.models.base import Base


class RunningChat(Base):
    __tablename__ = "running_chat"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False
    )
    session_pk: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        default=uuid.uuid4
    )
    user_input: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RunningChatTypeEnum.RUNNING.value
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    __table_args__ = (
        Index("ix_running_chat_session_started", "session_pk", "started_at"),
        Index("ix_running_chat_user_started", "user_id", "started_at"),
        Index("ix_running_chat_running","started_at"),
        Index(
            "ix_running_chat_running_expires",
            "expires_at",
            postgresql_where=text(f"status = '{RunningChatTypeEnum.RUNNING.value}'"),
        ),
        Index(
            "ix_running_chat_finished_expires",
            "expires_at",
            postgresql_where=text(f"status <> '{RunningChatTypeEnum.RUNNING.value}'"),
        ),
    )