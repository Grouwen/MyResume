from pydantic_settings import BaseSettings

from app.util import config_util


class JwtConfig(BaseSettings):
    secret_key: str
    access_token_expire_days: int = 7

    model_config = config_util.get_setting_config("JWT_")

jwt_config = JwtConfig()

if __name__ == '__main__':
    print(jwt_config.dict())