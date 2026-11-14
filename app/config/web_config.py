from pydantic_settings import BaseSettings

from app.util import config_util


class WebConfig(BaseSettings):
    host: str
    port: int
    forwarded_allow_ip:str = "127.0.0.1"

    model_config = config_util.get_setting_config("WEB_")

web_config = WebConfig()

if __name__ == '__main__':
    print(web_config.host)
    print(web_config.port)