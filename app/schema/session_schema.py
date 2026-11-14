import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionItem(BaseModel):

    model_config = ConfigDict(from_attributes=True)

    session_pk: uuid.UUID
    title: str | None = None
    message_count: int
    created_at: datetime
    last_active: datetime


class RenameSessionInput(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=255,
        description="新的会话标题",
    )

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("标题不能为空")
        return value


class MessageItem(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None