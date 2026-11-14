from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from app.core.enum.running_chat_type_enum import RunningChatTypeEnum
from app.models.account_verify import AccountVerify
from app.models.running_chat import RunningChat
from app.models.session import Session
from sqlalchemy import select, update, func
from sqlalchemy import select, update, func, delete

from app.models.user import User


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

    # 按 client_ip 查该 IP 未过期的验证码记录
    async def list_account_verify_by_ip(self, client_ip: str) -> list[AccountVerify]:
        stmt = (
            select(AccountVerify)
            .where(
                AccountVerify.client_ip == client_ip,
                AccountVerify.expires_at > func.now(),
            )
            .order_by(AccountVerify.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # 按 email 查最新一条未过期验证码：更早的码自动失效
    async def get_account_verify_by_email_and_code(self, email: str, code: int) -> AccountVerify | None:
        stmt = (
            select(AccountVerify)
            .where(
                AccountVerify.email == email,
                AccountVerify.expires_at > func.now(),
            )
            .order_by(AccountVerify.created_at.desc(), AccountVerify.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        record = result.scalars().first()
        if record is None or record.code != code:
            return None
        return record

    # 按 id 查用户
    async def get_user_by_id(self, user_id: int) -> User | None:
        stmt = select(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    # 按 email 查用户
    async def get_user_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.user_email == email)
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


    # ______________________ 插入 ______________________
    # 插入一条 running_chat 记录
    async def add_running_chat(self, running_chat: RunningChat) -> RunningChat:
        self._session.add(running_chat)
        await self._session.flush()
        return running_chat

    # 插入一条 account_verify 记录
    async def add_account_verify(self, account_verify: AccountVerify) -> AccountVerify:
        self._session.add(account_verify)
        await self._session.flush()
        return account_verify

    # 插入一条 user 记录
    async def add_user(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    # ______________________ 删除 ______________________
    async def delete_session(self, user_id: int, session_pk: uuid.UUID) -> bool:
        stmt = delete(Session).where(
            Session.user_id == user_id,
            Session.session_pk == session_pk,
        )
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def delete_running_chats(self, session_pk: uuid.UUID) -> int:
        stmt = delete(RunningChat).where(RunningChat.session_pk == session_pk)
        result = await self._session.execute(stmt)
        return result.rowcount or 0

    # 消费验证码：删除成功说明这次请求抢到了，并发下只有一个能成功
    async def delete_account_verify(self, verify_id: int) -> bool:
        stmt = delete(AccountVerify).where(AccountVerify.id == verify_id)
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    # 删除该邮箱所有未过期验证码（登录成功后调用）
    async def delete_account_verify_by_email(self, email: str) -> int:
        stmt = delete(AccountVerify).where(
            AccountVerify.email == email,
            AccountVerify.expires_at > func.now(),
        )
        result = await self._session.execute(stmt)
        return result.rowcount or 0

    # ______________________ 提交 | 回滚 ______________________
    async def commit(self):
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
