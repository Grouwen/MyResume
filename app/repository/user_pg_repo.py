from sqlalchemy import select

from app.models.user import User
from app.repository.pg_repository import PgRepository


class UserPgRepo(PgRepository):
    """user 表仓储"""

    # ______________________ 查询 ______________________
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

    # ______________________ 插入 ______________________
    # 插入一条 user 记录
    async def add_user(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user