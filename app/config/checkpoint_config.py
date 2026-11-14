from typing import Literal

from pydantic_settings import BaseSettings

from app.util import config_util


class CheckpointConfig(BaseSettings):
    backend: Literal["postgres", "memory"] = "postgres"
    setup_on_startup: bool = True
    pool_min_size: int = 1
    pool_max_size: int = 2

    model_config = config_util.get_setting_config("CHECKPOINT_")


checkpoint_config = CheckpointConfig()