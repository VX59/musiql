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
    password = settings.db_password
    DATABASE_URL = f"postgresql+asyncpg://jacob:{password}@jacob-server:5432/musiql"

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