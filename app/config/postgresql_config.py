from pydantic_settings import BaseSettings

from app.util import config_util


class PostgresqlConfig(BaseSettings):
    url: str
    pool_min_size: int = 2
    pool_max_size: int = 10
    echo: bool = False

    model_config = config_util.get_setting_config("POSTGRESQL_")

    @property
    def libpq_url(self) -> str:
        scheme, sep, rest = self.url.partition("://")
        if not sep:
            return self.url
        return f"{scheme.split('+', 1)[0]}://{rest}"


postgresql_config = PostgresqlConfig()

if __name__ == '__main__':
    print(postgresql_config.model_dump())