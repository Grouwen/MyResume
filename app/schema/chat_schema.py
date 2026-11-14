import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class ChatSchema(BaseModel):
    session_pk: uuid.UUID | None = None
    user_input: str
    time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    api_key: str
    model_name: str
    base_url: str