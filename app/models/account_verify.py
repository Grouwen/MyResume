from datetime import datetime

from sqlalchemy import String, Integer, DateTime, func, Index, BigInteger
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AccountVerify(Base):
    __tablename__ = "account_verify"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True
    )

    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    code: Mapped[int] = mapped_column(
        Integer,
        nullable=False
    )

    client_ip: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False
    )

    __table_args__ = (
        Index(
            "idx_account_verify_email_expires",
            "email",
            "expires_at",
        ),
        Index(
            "idx_account_verify_client_ip",
            "client_ip"
        ),
        Index(
            "idx_account_verify_code",
            "code"
        )
    )