from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from functools import lru_cache

from settings import get_settings, Settings


@lru_cache
def get_engine():

    settings: Settings = get_settings()

    DATABASE_URL = f"postgresql+asyncpg://{settings.db_user}:{settings.db_password}@{settings.db_domain}:{settings.db_port}/{settings.db_name}"

    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)

    return engine


@lru_cache
def get_session():
    engine = get_engine()

    async_session_maker = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return async_session_maker
