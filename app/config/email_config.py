from pydantic_settings import BaseSettings

from app.util import config_util


class EmailConfig(BaseSettings):
    sender_email: str
    host: str
    auth_code: str

    model_config = config_util.get_setting_config("EMAIL_")


email_config = EmailConfig()

if __name__ == '__main__':
    print(email_config.dict())