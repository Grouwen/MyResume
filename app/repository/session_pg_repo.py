import uuid

from sqlalchemy import delete, func, select

from app.models.session import Session
from app.repository.pg_repository import PgRepository


class SessionPgRepo(PgRepository):
    """session 表仓储"""

    # ______________________ 查询 ______________________
    # 按 user_id 查该用户所有会话
    async def list_sessions(
            self,
            user_id: int,
            limit: int = 20,
            offset: int = 0,
    ) -> list[Session]:
        stmt = (
            select(Session)
            .where(Session.user_id == user_id)
            .order_by(Session.last_active.desc())
            .limit(limit)
            .offset(offset)
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

    async def count_sessions(self, user_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Session)
            .where(Session.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one())

    # ______________________ 更新 ______________________
    # session_pk 存在则只更新 last_active，不存在则新建一条 session
    async def upsert_session(
        self,
        session_pk: uuid.UUID,
        user_id: int,
        thread_id: str,
        title: str | None = None,
    ) -> Session:
        existing = await self.get_session_by_session_pk(user_id, session_pk)
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

    async def rename_session(
            self,
            user_id: int,
            session_pk: uuid.UUID,
            title: str,
    ) -> Session | None:
        existing = await self.get_session_by_session_pk(user_id, session_pk)
        if existing is None:
            return None
        existing.title = title
        await self._session.flush()
        return existing

    # ______________________ 删除 ______________________
    async def delete_session(self, user_id: int, session_pk: uuid.UUID) -> bool:
        stmt = delete(Session).where(
            Session.user_id == user_id,
            Session.session_pk == session_pk,
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0