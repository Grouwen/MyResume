import re
import secrets
from datetime import datetime, timedelta, timezone
from fastapi import Request

from app.core.loger import get_logger
from app.models.account_verify import AccountVerify
from app.models.user import User
from app.repository.pg_repository import PgRepository
from app.schema.result import Result
from app.schema.user_schema import LoginUserSchema, UserSchema
from app.util.email_util import send_email
from app.util.jwt_utile import genert_token
from app.util.request_util import get_client_ip

logger = get_logger(__name__)

PER_IP_MAX_VERIFY = 10

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

class UserService:
    def __init__(self,
                 pg_repository: PgRepository):
        self._pg_repository = pg_repository

    async def send_email_service(self, user_email,request: Request):
        user_email = handle_email(user_email)
        if not user_email:
            return Result.error("邮箱格式错误")

        subject = "MyResume 验证码"
        html_content = """
        <p>你好，你正在进行 <b>MyResume</b> 邮箱验证。</p>
        <p>你的验证码为: <b>{code}</b></p>
        <p>验证码 {expires_min} 分钟内有效，如果不是本人操作，请忽略。</p>
        """
        client_ip = get_client_ip(request)
        if not client_ip:
            return Result.error("ip校验失败，请稍后再试")

        code = secrets.randbelow(900000) + 100000
        expires_min = 5

        # 根据ip查看expires_min内发送的次数
        all_record = await self._pg_repository.list_account_verify_by_ip(client_ip)
        if len(all_record) >= PER_IP_MAX_VERIFY:
            return Result.error(f"{expires_min}分钟内获取验证码次数过多，请稍后")

        # 插入记录
        acco_ver = AccountVerify(
            email=user_email,
            code=code,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=expires_min),
            client_ip=client_ip
        )

        await self._pg_repository.add_account_verify(acco_ver)

        await self._pg_repository.commit()

        try:
            await send_email(user_email, subject, html_content.format(code=code,
                                                                      expires_min=expires_min))

            return Result.ok({})
        except Exception as e:
            logger.exception(f"发送验证码失败 email={user_email},exc={type(e).__name__}: {e}")
            try:
                await self._pg_repository.delete_account_verify(acco_ver.id)
                await self._pg_repository.commit()
            except Exception:
                logger.exception(f"清理未送达验证码失败 id={acco_ver.id}")
            return Result.error(f"发送邮件失败{type(e).__name__}")

    async def login(self, login_user_schema: LoginUserSchema):
        user_email = handle_email(login_user_schema.user_email)
        if not user_email:
            return Result.error("邮箱格式错误")

        account_verify = await self._pg_repository.get_account_verify_by_email_and_code(
            user_email,
            login_user_schema.code
        )
        if not account_verify:
            return Result.error("验证码或邮箱错误")

        if not await self._pg_repository.delete_account_verify(account_verify.id):
            return Result.error("验证码或邮箱错误")

        user = await self._pg_repository.get_user_by_email(user_email)
        if user is None:
            user = await self._pg_repository.add_user(User(
                user_nick_name="",
                user_email=user_email,
            ))
            user.user_nick_name = f"用户{user.id:06d}"

        await self._pg_repository.commit()

        user_schema = UserSchema(
            user_id=user.id,
            user_name=user.user_nick_name
        )
        return Result.ok({"token": genert_token(user_schema)})

def handle_email(email:str)->str|None:
    email = email.strip().lower()
    if not EMAIL_RE.fullmatch(email):
        return None

    return email
