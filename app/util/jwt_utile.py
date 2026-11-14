from typing import Dict, Any

import jwt
from datetime import datetime, timedelta, timezone

from app.config.jwt_util import jwt_config
from app.schema.user_schema import UserSchema

ALGORITHM = "HS256"

def genert_token(user: UserSchema):
    """
    生成 JWT Token
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=jwt_config.access_token_expire_days
    )

    payload = {
        **user.model_dump(),
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }

    token = jwt.encode(
        payload,
        jwt_config.secret_key,
        algorithm=ALGORITHM
    )

    return token


def verify_jwt(token: str)->Dict[str, Any]:
    """
    验证 JWT Token
    """
    return jwt.decode(token, jwt_config.secret_key, algorithms=[ALGORITHM])

def parser_to_user(token: str)->UserSchema:
    payload = verify_jwt(token)
    return UserSchema(**payload)
