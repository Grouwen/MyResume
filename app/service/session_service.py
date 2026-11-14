import uuid

from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph

from app.schema.session_schema import MessageItem
from app.core.app_exceptions import AppException, AppExceptionCode
from app.core.loger import get_logger
from app.models.session import Session
from app.repository.running_chat_pg_repo import RunningChatPgRepo
from app.repository.session_pg_repo import SessionPgRepo
from app.schema.user_schema import UserSchema

logger = get_logger(__name__)


class SessionService:

    def __init__(
            self,
            session_pg_repo: SessionPgRepo,
            running_chat_pg_repo: RunningChatPgRepo,
            checkpointer: BaseCheckpointSaver,
            graph: CompiledStateGraph,
    ):
        self._session_pg_repo = session_pg_repo
        self._running_chat_pg_repo = running_chat_pg_repo
        self._checkpointer = checkpointer
        self._graph = graph

    async def list_sessions(
            self,
            user_schema: UserSchema,
            page: int,
            size: int,
    ) -> tuple[list[Session], int]:
        total = await self._session_pg_repo.count_sessions(user_schema.user_id)
        sessions = await self._session_pg_repo.list_sessions(
            user_schema.user_id,
            limit=size,
            offset=(page - 1) * size,
        )
        return sessions, total

    async def rename_session(
            self,
            user_schema: UserSchema,
            session_pk: uuid.UUID,
            title: str,
    ) -> str:
        session = await self._session_pg_repo.rename_session(
            user_schema.user_id,
            session_pk,
            title,
        )
        if session is None:
            raise AppException(AppExceptionCode.NOT_FOUND, "会话不存在")

        await self._session_pg_repo.commit()
        return title

    async def delete_session(
            self,
            user_schema: UserSchema,
            session_pk: uuid.UUID,
    ) -> None:
        session = await self._session_pg_repo.get_session_by_session_pk(
            user_schema.user_id,
            session_pk,
        )
        if session is None:
            raise AppException(AppExceptionCode.NOT_FOUND, "会话不存在")

        if await self._running_chat_pg_repo.has_running_chat(session_pk):
            raise AppException(
                AppExceptionCode.CONFLICT,
                "当前会话正在对话中，请稍后再删除",
            )

        # commit 后 ORM 对象会过期，先把 thread_id 取出来
        thread_id = session.thread_id

        await self._running_chat_pg_repo.delete_running_chats(session_pk)
        deleted = await self._session_pg_repo.delete_session(
            user_schema.user_id,
            session_pk,
        )
        if not deleted:
            raise AppException(AppExceptionCode.NOT_FOUND, "会话不存在")
        await self._session_pg_repo.commit()

        # DB 已提交；checkpoint 删失败只留孤儿数据，不能让接口失败
        try:
            await self._checkpointer.adelete_thread(thread_id)
        except Exception:
            logger.exception("删除 checkpoint 失败 thread_id=%s", thread_id)

    async def get_messages(
            self,
            user_schema: UserSchema,
            session_pk: uuid.UUID,
            limit: int = 100,
            include_tools: bool = False,
    ) -> tuple[list[MessageItem], int, bool, str | None]:
        session = await self._session_pg_repo.get_session_by_session_pk(
            user_schema.user_id,
            session_pk,
        )
        if session is None:
            raise AppException(AppExceptionCode.NOT_FOUND, "会话不存在")

        snapshot = await self._graph.aget_state(
            {"configurable": {"thread_id": session.thread_id}}
        )

        raw_messages: list[BaseMessage] = (snapshot.values or {}).get("messages", [])
        items: list[MessageItem] = []
        for message in raw_messages:
            item = _message_to_item(message, include_tools)
            if item is not None:
                items.append(item)

        total = len(items)
        has_more = total > limit
        if has_more:
            items = items[-limit:]

        return items, total, has_more, snapshot.created_at


def _content_to_text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts)

def _message_to_item(message: BaseMessage, include_tools: bool) -> MessageItem | None:
    if isinstance(message, HumanMessage):
        return MessageItem(role="user", content=_content_to_text(message.content))

    if isinstance(message, AIMessage):
        text = _content_to_text(message.content)
        tool_calls = message.tool_calls or []
        # 不带工具时，跳过“只发起工具调用、没有正文”的中间 AI 消息
        if not include_tools and not text and tool_calls:
            return None
        return MessageItem(
            role="assistant",
            content=text,
            tool_calls=[
                {
                    "name": call.get("name"),
                    "args": call.get("args"),
                    "id": call.get("id"),
                }
                for call in tool_calls
            ] if include_tools else None,
        )

    if isinstance(message, ToolMessage):
        if not include_tools:
            return None
        return MessageItem(
            role="tool",
            content=_content_to_text(message.content),
            name=message.name,
            tool_call_id=message.tool_call_id,
        )

    return None