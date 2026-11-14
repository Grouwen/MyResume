from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine

from app.config.postgresql_config import postgresql_config

_pgsql_engine:AsyncEngine | None = None

async def init_pgsql_session_factory()->async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        postgresql_config.url,
        echo=postgresql_config.echo,
        pool_size=postgresql_config.pool_min_size,
        max_overflow=postgresql_config.pool_max_size - postgresql_config.pool_min_size,
        pool_pre_ping=True
    )

    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        autoflush=False
    )

    global _pgsql_engine
    _pgsql_engine = engine

    return session_factory

async def close_pgsql_engine():
    global _pgsql_engine
    if _pgsql_engine:
        await _pgsql_engine.dispose()