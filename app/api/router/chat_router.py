from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.sse import ServerSentEvent, EventSourceResponse

from app.core.dependencies import get_chat_service
from app.core.enum.sse_enum import AgentEnum
from app.core.loger import get_logger
from app.core.token_auth import get_current_user
from app.schema.chat_schema import ChatSchema
from app.schema.result import Result
from app.schema.user_schema import UserSchema
from app.service.chat_service import ChatService

chat_router = APIRouter(prefix="/api/chat", tags=["对话"])

logger = get_logger(__name__)

@chat_router.post("", response_class=EventSourceResponse)
async def chat(chat_schema:ChatSchema,
               chat_service: ChatService = Depends(get_chat_service),
               user_schema:UserSchema = Depends(get_current_user)) -> AsyncIterator[ServerSentEvent]:
    try:
        async for event in chat_service.chat(chat_schema,user_schema):
            yield event
    except Exception as e:
        logger.error(f"错误：{str(e)}")
        yield ServerSentEvent(
            event=AgentEnum.ERROR.value,
            data=Result.error(f"错误：{type(e).__name__}: {e}")
        )