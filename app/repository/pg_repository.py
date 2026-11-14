from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from app.core.enum.running_chat_type_enum import RunningChatTypeEnum
from app.models.running_chat import RunningChat
from app.models.session import Session
from sqlalchemy import select, update, func


class PgRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    # ______________________ 查询 ______________________
    # 按 user_id 查该用户所有 running 状态的会话
    async def list_running_chats(self, user_id: int) -> list[RunningChat]:
        stmt = (
            select(RunningChat)
            .where(
                RunningChat.user_id == user_id,
                RunningChat.status == RunningChatTypeEnum.RUNNING.value,
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # 按 user_id 查该用户所有会话
    async def list_sessions(self, user_id: int) -> list[Session]:
        stmt = (
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.last_active.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # 按 user_id + session_pk 查单个会话
    async def get_session_by_session_pk(self, user_id: int, session_pk: uuid.UUID) -> Session | None:
        stmt = (
            select(Session)
            .where(
                Session.user_id == user_id,
                Session.session_pk == session_pk,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()


    # ______________________ 更新 ______________________
    # session_pk 存在则只更新 last_active，不存在则新建一条 session
    async def upsert_session(
        self,
        session_pk: uuid.UUID,
        user_id: int,
        thread_id: str,
        title: str | None = None,
    ) -> Session:
        existing = await self.get_session_by_session_pk(user_id,session_pk)
        if existing is not None:
            existing.last_active = func.now()
            existing.message_count = Session.message_count + 1
            await self._session.flush()
            return existing

        new_session = Session(
            session_pk=session_pk,
            user_id=user_id,
            thread_id=thread_id,
            title=title,
            message_count=1
        )
        self._session.add(new_session)
        await self._session.flush()
        return new_session

    # 按 session_pk 更新 running_chat 的状态（仅当当前状态为 running）
    async def update_running_chat_status(
            self,
            session_pk: uuid.UUID,
            status: RunningChatTypeEnum,
    ) -> int:
        stmt = (
            update(RunningChat)
            .where(
                RunningChat.session_pk == session_pk,
                RunningChat.status == RunningChatTypeEnum.RUNNING.value,
            )
            .values(status=status.value)
        )
        result = await self._session.execute(stmt)
        return result.rowcount


    # ______________________ 插入 ______________________
    # 插入一条 running_chat 记录
    async def add_running_chat(self, running_chat: RunningChat) -> RunningChat:
        self._session.add(running_chat)
        await self._session.flush()
        return running_chat

    # ______________________ 提交 ______________________
    async def commit(self):
        await self._session.commit()
