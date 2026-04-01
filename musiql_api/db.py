from sqlalchemy.orm import sessionmaker
from configparser import ConfigParser
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from functools import lru_cache

from musiql_api.settings import get_settings, Settings

@lru_cache
def get_engine():

    settings:Settings = get_settings()

    config = ConfigParser()
    config.read("alembic.ini")

    DATABASE_URL = f"postgresql+asyncpg://{settings.db_user}:{settings.db_password}@{settings.db_domain}:{settings.db_port}/{settings.db_name}"

    engine = create_async_engine(DATABASE_URL, echo=True)

    return engine

@lru_cache
def get_session():
    engine = get_engine()

    async_session = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return async_session