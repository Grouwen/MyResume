import jwt
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import ValidationError

from app.core.app_exceptions import AuthException
from app.schema.user_schema import UserSchema
from app.util.jwt_utile import parser_to_user

security = HTTPBearer(auto_error=False)

# 验证是否登录，登录则返回用户信息
async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
)->UserSchema:

    if not credentials:
        return UserSchema(
            user_id=-1,
            user_name=""
        )

    token = credentials.credentials
    token = token.strip()

    # 请求头token
    if not token:
        return UserSchema(
            user_id=-1,
            user_name=""
        )

    # token检验
    try:
        user_schema = parser_to_user(token)

    except jwt.ExpiredSignatureError:
        raise AuthException("登录过期")

    except jwt.InvalidTokenError:
        raise AuthException("登录凭证无效")

    except ValidationError:
        raise AuthException("登录凭证解析错误")


    return user_schema

async def require_login(
    user_schema: UserSchema = Depends(get_current_user),
) -> UserSchema:
    """需要登录的接口用它；匿名用户（user_id == -1）直接 401。"""
    if user_schema.user_id == -1:
        raise AuthException("请先登录")
    return user_schema