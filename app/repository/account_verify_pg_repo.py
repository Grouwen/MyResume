from sqlalchemy import delete, func, select

from app.models.account_verify import AccountVerify
from app.repository.pg_repository import PgRepository


class AccountVerifyPgRepo(PgRepository):
    """account_verify 表仓储（邮箱验证码）"""

    # ______________________ 查询 ______________________
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

    # ______________________ 插入 ______________________
    # 插入一条 account_verify 记录
    async def add_account_verify(self, account_verify: AccountVerify) -> AccountVerify:
        self._session.add(account_verify)
        await self._session.flush()
        return account_verify

    # ______________________ 删除 ______________________
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