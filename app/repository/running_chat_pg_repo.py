import uuid

from sqlalchemy import delete, func, select, update

from app.core.enum.running_chat_type_enum import RunningChatTypeEnum
from app.models.running_chat import RunningChat
from app.repository.pg_repository import PgRepository


class RunningChatPgRepo(PgRepository):
    """running_chat 表仓储"""

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

    async def has_running_chat(self, session_pk: uuid.UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(RunningChat)
            .where(
                RunningChat.session_pk == session_pk,
                RunningChat.status == RunningChatTypeEnum.RUNNING.value,
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one()) > 0

    # ______________________ 更新 ______________________
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

    # ______________________ 删除 ______________________
    async def delete_running_chats(self, session_pk: uuid.UUID) -> int:
        stmt = delete(RunningChat).where(RunningChat.session_pk == session_pk)
        result = await self._session.execute(stmt)
        return result.rowcount or 0