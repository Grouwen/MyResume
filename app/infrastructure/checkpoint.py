from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.config.checkpoint_config import checkpoint_config
from app.config.postgresql_config import postgresql_config
from app.core.loger import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    pool = AsyncConnectionPool(
        conninfo=postgresql_config.libpq_url,
        min_size=checkpoint_config.pool_min_size,
        max_size=checkpoint_config.pool_max_size,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )

    try:
        await pool.open(wait=True, timeout=10)

        saver = AsyncPostgresSaver(pool)
        if checkpoint_config.setup_on_startup:
            await saver.setup()
        logger.info(
            "checkpointer 已就绪：%s（pool %s~%s）",
            type(saver).__name__,
            checkpoint_config.pool_min_size,
            checkpoint_config.pool_max_size,
        )
        yield saver
    finally:
        await pool.close()