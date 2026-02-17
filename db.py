from sqlalchemy.orm import sessionmaker
from configparser import ConfigParser
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
import os
config = ConfigParser()
config.read("alembic.ini")
password = os.getenv("DB_PASSWORD")
DATABASE_URL = f"postgresql+asyncpg://jacob:{password}@jacob-server:5432/musiql"

engine = create_async_engine(DATABASE_URL, echo=True)

async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)